from collections.abc import Iterable
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.evidence.conflicts import ConflictFact, load_conflicts
from tracerisk.evidence.models import Evidence
from tracerisk.rules.business import BusinessFact, evaluate_business_abnormal
from tracerisk.rules.common import RuleEvaluation, RuleScore
from tracerisk.rules.intellectual_property import (
    IntellectualPropertyFact,
    evaluate_core_ip_anomaly,
)
from tracerisk.rules.judicial import JudicialFact, evaluate_judicial_open_cases
from tracerisk.rules.models import RuleFinding, RuleFindingEvidence
from tracerisk.rules.news import NewsFact, evaluate_negative_news

RULE_CODE_ORDER = (
    "JUDICIAL_OPEN_CASE_V1",
    "BUSINESS_ABNORMAL_V1",
    "NEGATIVE_NEWS_V1",
    "CORE_IP_ANOMALY_V1",
)
_RULE_ORDER = {code: position for position, code in enumerate(RULE_CODE_ORDER)}
_RULE_MATCH_SCORES = {
    "JUDICIAL_OPEN_CASE_V1": 35,
    "BUSINESS_ABNORMAL_V1": 25,
    "NEGATIVE_NEWS_V1": 20,
    "CORE_IP_ANOMALY_V1": 15,
}


async def _load_evidence(
    session: AsyncSession,
    *,
    company_id: UUID,
    as_of_date: date,
    source_type: str,
    risk_type: str,
    fact_types: tuple[str, ...],
) -> list[Evidence]:
    return list(
        (
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.company_id == company_id,
                    Evidence.source_type == source_type,
                    Evidence.risk_type == risk_type,
                    Evidence.fact_type.in_(fact_types),
                    Evidence.observed_at <= as_of_date,
                )
                .order_by(Evidence.fact_key, Evidence.observed_at, Evidence.id)
            )
        ).all()
    )


async def evaluate_judicial_rule(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> RuleEvaluation:
    records = await _load_evidence(
        session,
        company_id=company_id,
        as_of_date=as_of_date,
        source_type="judicial",
        risk_type="judicial",
        fact_types=("judicial_case_status",),
    )
    return evaluate_judicial_open_cases(
        [
            JudicialFact(
                evidence_id=record.id,
                case_number=record.fact_key,
                status=record.fact_value,
                observed_at=record.observed_at,
                content_hash=record.content_hash,
            )
            for record in records
        ],
        as_of_date=as_of_date,
    )


async def evaluate_business_rule(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> RuleEvaluation:
    records = await _load_evidence(
        session,
        company_id=company_id,
        as_of_date=as_of_date,
        source_type="business",
        risk_type="business",
        fact_types=("business_abnormal_status",),
    )
    return evaluate_business_abnormal(
        [
            BusinessFact(
                evidence_id=record.id,
                abnormal_record_id=record.fact_key,
                status=record.fact_value,
                observed_at=record.observed_at,
                content_hash=record.content_hash,
            )
            for record in records
        ],
        as_of_date=as_of_date,
    )


async def evaluate_news_rule(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> RuleEvaluation:
    window_start = date.fromordinal(as_of_date.toordinal() - 364)
    records = list(
        (
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.company_id == company_id,
                    Evidence.source_type == "news",
                    Evidence.risk_type == "news",
                    Evidence.fact_type == "negative_news_story",
                    Evidence.observed_at >= window_start,
                    Evidence.observed_at <= as_of_date,
                )
                .order_by(Evidence.fact_key, Evidence.observed_at, Evidence.id)
            )
        ).all()
    )
    return evaluate_negative_news(
        [
            NewsFact(
                evidence_id=record.id,
                canonical_story_id=record.fact_key,
                polarity=record.polarity,
                observed_at=record.observed_at,
                content_hash=record.content_hash,
            )
            for record in records
        ],
        as_of_date=as_of_date,
    )


async def evaluate_intellectual_property_rule(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> RuleEvaluation:
    records = await _load_evidence(
        session,
        company_id=company_id,
        as_of_date=as_of_date,
        source_type="intellectual_property",
        risk_type="intellectual_property",
        fact_types=("core_ip_status", "noncore_ip_status"),
    )
    return evaluate_core_ip_anomaly(
        [
            IntellectualPropertyFact(
                evidence_id=record.id,
                ip_record_id=record.fact_key,
                status=record.fact_value,
                fact_type=record.fact_type,
                observed_at=record.observed_at,
                content_hash=record.content_hash,
            )
            for record in records
        ],
        as_of_date=as_of_date,
    )


def aggregate_rule_evaluations(
    evaluations: Iterable[RuleEvaluation],
    *,
    non_rule_inputs: Iterable[object] = (),
) -> RuleScore:
    # Surrounding AI/review data is accepted at this boundary but never projected.
    del non_rule_inputs
    by_code: dict[str, RuleEvaluation] = {}
    for evaluation in evaluations:
        if evaluation.rule_code not in _RULE_ORDER:
            raise ValueError(f"Unknown rule code: {evaluation.rule_code}")
        if evaluation.rule_code in by_code:
            raise ValueError(f"Duplicate rule code: {evaluation.rule_code}")
        if not 0 <= evaluation.score <= 95:
            raise ValueError("Rule score is outside 0..95")
        by_code[evaluation.rule_code] = evaluation
    ordered = tuple(by_code[code] for code in RULE_CODE_ORDER if code in by_code)
    total = sum(evaluation.score for evaluation in ordered)
    if not 0 <= total <= 95:
        raise ValueError("Rule total is outside 0..95")
    for evaluation in ordered:
        if evaluation.score not in {0, _RULE_MATCH_SCORES[evaluation.rule_code]}:
            raise ValueError(f"Invalid fixed score for rule: {evaluation.rule_code}")
    return RuleScore(total_score=total, evaluations=ordered)


async def evaluate_all_rules(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> RuleScore:
    policy = await load_conflicts(session, company_id=company_id, as_of_date=as_of_date)
    return evaluate_all_rules_from_facts(policy.resolved_facts, as_of_date=as_of_date)


def evaluate_all_rules_from_facts(
    records: Iterable[ConflictFact], *, as_of_date: date
) -> RuleScore:
    """Evaluate supplied frozen facts; reference hashes identify Evidence versions."""
    rows = tuple(records)
    evaluations = [
        evaluate_judicial_open_cases(
            [
                JudicialFact(
                    evidence_id=row.evidence_id,
                    case_number=row.fact_key,
                    status=row.fact_value,
                    observed_at=row.observed_at,
                    content_hash=row.content_hash,
                )
                for row in rows
                if row.risk_type == "judicial"
                and row.fact_type == "judicial_case_status"
            ],
            as_of_date=as_of_date,
        ),
        evaluate_business_abnormal(
            [
                BusinessFact(
                    evidence_id=row.evidence_id,
                    abnormal_record_id=row.fact_key,
                    status=row.fact_value,
                    observed_at=row.observed_at,
                    content_hash=row.content_hash,
                )
                for row in rows
                if row.risk_type == "business"
                and row.fact_type == "business_abnormal_status"
            ],
            as_of_date=as_of_date,
        ),
        evaluate_negative_news(
            [
                NewsFact(
                    evidence_id=row.evidence_id,
                    canonical_story_id=row.fact_key,
                    polarity=row.polarity,
                    observed_at=row.observed_at,
                    content_hash=row.content_hash,
                )
                for row in rows
                if row.risk_type == "news" and row.fact_type == "negative_news_story"
            ],
            as_of_date=as_of_date,
        ),
        evaluate_core_ip_anomaly(
            [
                IntellectualPropertyFact(
                    evidence_id=row.evidence_id,
                    ip_record_id=row.fact_key,
                    status=row.fact_value,
                    fact_type=row.fact_type,
                    observed_at=row.observed_at,
                    content_hash=row.content_hash,
                )
                for row in rows
                if row.risk_type == "intellectual_property"
                and row.fact_type in {"core_ip_status", "noncore_ip_status"}
            ],
            as_of_date=as_of_date,
        ),
    ]
    return aggregate_rule_evaluations(evaluations)


async def save_rule_finding(
    session: AsyncSession, *, run_id: UUID, evaluation: RuleEvaluation
) -> RuleFinding | None:
    existing = await session.scalar(
        select(RuleFinding).where(
            RuleFinding.run_id == run_id,
            RuleFinding.rule_code == evaluation.rule_code,
        )
    )
    if not evaluation.is_match:
        if existing is not None:
            raise ValueError("Existing rule finding differs from this evaluation")
        return None
    finding_id = await session.scalar(
        insert(RuleFinding)
        .values(
            run_id=run_id,
            rule_code=evaluation.rule_code,
            rule_version=evaluation.rule_version,
            score=evaluation.score,
            parameters=evaluation.parameters,
        )
        .on_conflict_do_nothing(index_elements=["run_id", "rule_code"])
        .returning(RuleFinding.id)
    )
    if finding_id is not None:
        session.add_all(
            RuleFindingEvidence(
                rule_finding_id=finding_id,
                evidence_id=reference.evidence_id,
                content_hash=reference.content_hash,
            )
            for reference in evaluation.evidence_references
        )
        await session.flush()
    finding = existing or await session.scalar(
        select(RuleFinding).where(
            RuleFinding.run_id == run_id,
            RuleFinding.rule_code == evaluation.rule_code,
        )
    )
    if finding is None:
        raise RuntimeError("Rule finding was not saved")
    if (
        finding.rule_version != evaluation.rule_version
        or finding.score != evaluation.score
        or finding.parameters != evaluation.parameters
    ):
        raise ValueError("Existing rule finding differs from this evaluation")
    saved_references = (
        await session.scalars(
            select(RuleFindingEvidence).where(
                RuleFindingEvidence.rule_finding_id == finding.id
            )
        )
    ).all()
    expected_references = {
        (reference.evidence_id, reference.content_hash)
        for reference in evaluation.evidence_references
    }
    if {
        (reference.evidence_id, reference.content_hash)
        for reference in saved_references
    } != expected_references:
        raise ValueError("Existing rule finding has different evidence references")
    return finding


async def save_rule_findings(
    session: AsyncSession, *, run_id: UUID, score: RuleScore
) -> tuple[RuleFinding, ...]:
    saved: list[RuleFinding] = []
    for evaluation in score.evaluations:
        finding = await save_rule_finding(session, run_id=run_id, evaluation=evaluation)
        if finding is not None:
            saved.append(finding)
    return tuple(saved)
