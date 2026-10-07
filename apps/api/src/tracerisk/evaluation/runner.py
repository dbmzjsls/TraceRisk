"""Run fixed Mock/Fake measurements against an already isolated database."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.models import AIFinding
from tracerisk.ai.schemas import RiskFindingDraft, StructuredRiskFindings
from tracerisk.ai.validation import validate_findings
from tracerisk.auth.models import User
from tracerisk.config import Settings
from tracerisk.evaluation.metrics import MetricResult, ratio_metric
from tracerisk.evidence.conflicts import ConflictFact, evaluate_conflicts
from tracerisk.evidence.service import load_frozen_retrieval
from tracerisk.investigations.models import WorkflowRun
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.evaluation import evaluate as evaluate_retrieval
from tracerisk.retrieval.evaluation import source_snapshot_hash
from tracerisk.reviews.service import FindingReview, submit_review
from tracerisk.rules.engine import RULE_CODE_ORDER, evaluate_all_rules_from_facts
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.workflows.spike_graph import execute_workflow

BASELINE = date(2026, 9, 11)
POINTS = dict(zip(RULE_CODE_ORDER, (35, 25, 20, 15), strict=True))


@dataclass(frozen=True)
class FactLabel:
    source: str
    risk: str
    fact_type: str
    key: str
    value: str
    day_offset: int = 0
    polarity: str = "neutral"


@dataclass(frozen=True)
class RuleCase:
    case_id: str
    domain: str
    facts: tuple[FactLabel, ...]
    expected_codes: tuple[str, ...]
    expected_keys: tuple[str, ...]
    conflict: bool = False


@dataclass(frozen=True)
class FrozenCitation:
    risk_type: str
    chunk_id: str
    evidence_id: str
    content_hash: str
    content: str


def final_coverage_samples(
    final_items: object, frozen: Sequence[FrozenCitation]
) -> dict[str, bool]:
    """Credit a final finding when any actual final citation is frozen and valid."""
    if not isinstance(final_items, list):
        raise ValueError("Report final findings are malformed")
    by_chunk = {(item.risk_type, item.chunk_id): item for item in frozen}
    samples: dict[str, bool] = {}
    for item in final_items:
        if not isinstance(item, dict):
            raise ValueError("Report final finding is malformed")
        key = item.get("finding_key")
        risk_type = item.get("risk_type")
        citations = item.get("citations")
        if (
            not isinstance(key, str)
            or not key.strip()
            or key in samples
            or not isinstance(risk_type, str)
            or not isinstance(citations, list)
        ):
            raise ValueError("Final finding identity or citations are malformed")

        def valid(citation: object, finding_risk_type: str) -> bool:
            if not isinstance(citation, dict):
                return False
            chunk = by_chunk.get((finding_risk_type, str(citation.get("chunk_id"))))
            excerpt = citation.get("quoted_excerpt")
            return (
                chunk is not None
                and str(citation.get("evidence_id")) == chunk.evidence_id
                and citation.get("content_hash") == chunk.content_hash
                and isinstance(excerpt, str)
                and bool(excerpt.strip())
                and excerpt in chunk.content
            )

        samples[key] = any(valid(citation, risk_type) for citation in citations)
    return samples


def _f(
    source: str,
    risk: str,
    fact_type: str,
    key: str,
    value: str,
    day_offset: int = 0,
    polarity: str = "neutral",
) -> FactLabel:
    return FactLabel(source, risk, fact_type, key, value, day_offset, polarity)


RULE_CASES: tuple[RuleCase, ...] = (
    RuleCase(
        "business-active",
        "business",
        (_f("business", "business", "business_abnormal_status", "B-1", "active"),),
        ("BUSINESS_ABNORMAL_V1",),
        ("B-1",),
    ),
    RuleCase(
        "business-resolved",
        "business",
        (
            _f("business", "business", "business_abnormal_status", "B-1", "active", -1),
            _f("business", "business", "business_abnormal_status", "B-1", "resolved"),
        ),
        (),
        (),
    ),
    RuleCase(
        "business-future-resolution",
        "business",
        (
            _f("business", "business", "business_abnormal_status", "B-1", "active"),
            _f(
                "business", "business", "business_abnormal_status", "B-1", "resolved", 1
            ),
        ),
        ("BUSINESS_ABNORMAL_V1",),
        ("B-1",),
    ),
    RuleCase("source-insufficient", "business", (), (), ()),
    RuleCase(
        "judicial-open",
        "judicial",
        (_f("judicial", "judicial", "judicial_case_status", "J-1", "open"),),
        ("JUDICIAL_OPEN_CASE_V1",),
        ("J-1",),
    ),
    RuleCase(
        "judicial-closed",
        "judicial",
        (
            _f("judicial", "judicial", "judicial_case_status", "J-1", "open", -1),
            _f("judicial", "judicial", "judicial_case_status", "J-1", "closed"),
        ),
        (),
        (),
    ),
    RuleCase(
        "judicial-source-conflict",
        "judicial",
        (
            _f("judicial", "judicial", "judicial_case_status", "J-1", "open"),
            _f("news", "judicial", "judicial_case_status", "J-1", "closed"),
        ),
        ("JUDICIAL_OPEN_CASE_V1",),
        ("J-1",),
        True,
    ),
    RuleCase(
        "news-d364-included",
        "news",
        (
            _f(
                "news",
                "news",
                "negative_news_story",
                "N-1",
                "negative",
                -364,
                "negative",
            ),
            _f("news", "news", "negative_news_story", "N-2", "negative", 0, "negative"),
        ),
        ("NEGATIVE_NEWS_V1",),
        ("N-1", "N-2"),
    ),
    RuleCase(
        "news-d365-excluded",
        "news",
        (
            _f(
                "news",
                "news",
                "negative_news_story",
                "N-1",
                "negative",
                -365,
                "negative",
            ),
            _f("news", "news", "negative_news_story", "N-2", "negative", 0, "negative"),
        ),
        (),
        (),
    ),
    RuleCase(
        "news-deduplicated",
        "news",
        (
            _f(
                "news", "news", "negative_news_story", "N-1", "negative", -1, "negative"
            ),
            _f("news", "news", "negative_news_story", "N-1", "negative", 0, "negative"),
        ),
        (),
        (),
    ),
    RuleCase(
        "ip-core-invalid",
        "intellectual_property",
        (
            _f(
                "intellectual_property",
                "intellectual_property",
                "core_ip_status",
                "IP-1",
                "invalid",
            ),
        ),
        ("CORE_IP_ANOMALY_V1",),
        ("IP-1",),
    ),
    RuleCase(
        "ip-noncore-excluded",
        "intellectual_property",
        (
            _f(
                "intellectual_property",
                "intellectual_property",
                "noncore_ip_status",
                "IP-1",
                "invalid",
            ),
        ),
        (),
        (),
    ),
    RuleCase(
        "ip-core-resolved",
        "intellectual_property",
        (
            _f(
                "intellectual_property",
                "intellectual_property",
                "core_ip_status",
                "IP-1",
                "ownership_dispute",
                -1,
            ),
            _f(
                "intellectual_property",
                "intellectual_property",
                "core_ip_status",
                "IP-1",
                "valid",
            ),
        ),
        (),
        (),
    ),
)


def evaluate_rule_cases(snapshot_hash: str) -> dict[str, object]:
    results: list[dict[str, object]] = []
    overall: dict[str, bool] = {}
    domains: dict[str, dict[str, bool]] = {}
    for index, case in enumerate(RULE_CASES, start=1):
        facts = tuple(
            ConflictFact(
                evidence_id=UUID(int=index * 100 + position),
                company_id=UUID(int=1),
                source_type=label.source,
                risk_type=label.risk,
                fact_type=label.fact_type,
                fact_key=label.key,
                fact_value=label.value,
                observed_at=BASELINE + timedelta(days=label.day_offset),
                content_hash="a" * 64,
                polarity=label.polarity,
            )
            for position, label in enumerate(case.facts, start=1)
        )
        conflicts = evaluate_conflicts(facts, as_of_date=BASELINE)
        score = evaluate_all_rules_from_facts(
            conflicts.resolved_facts, as_of_date=BASELINE
        )
        actual_codes = tuple(e.rule_code for e in score.evaluations if e.is_match)
        actual_keys = tuple(
            key for e in score.evaluations for key in e.matched_fact_keys
        )
        expected_codes = tuple(
            code for code in RULE_CODE_ORDER if code in case.expected_codes
        )
        expected_points = {
            code: POINTS[code] if code in expected_codes else 0
            for code in RULE_CODE_ORDER
        }
        actual_points = {e.rule_code: e.score for e in score.evaluations}
        passed = (
            actual_codes == expected_codes
            and actual_keys == case.expected_keys
            and actual_points == expected_points
            and score.total_score == sum(expected_points.values())
            and conflicts.requires_review == case.conflict
        )
        overall[case.case_id] = passed
        domains.setdefault(case.domain, {})[case.case_id] = passed
        results.append(
            {
                "case_id": case.case_id,
                "domain": case.domain,
                "expected_codes": expected_codes,
                "actual_codes": actual_codes,
                "expected_points": expected_points,
                "actual_points": actual_points,
                "expected_keys": case.expected_keys,
                "actual_keys": actual_keys,
                "expected_conflict": case.conflict,
                "actual_conflict": conflicts.requires_review,
                "actual_total": score.total_score,
                "passed": passed,
            }
        )
    return {
        "overall": ratio_metric(
            "rule_exact_match_accuracy",
            "exact matching cases / labeled cases",
            overall,
            source_snapshot_sha256=snapshot_hash,
        ),
        "per_domain": {
            domain: ratio_metric(
                f"rule_exact_match_{domain}",
                "exact matching cases / labeled domain cases",
                samples,
                source_snapshot_sha256=snapshot_hash,
            )
            for domain, samples in domains.items()
        },
        "cases": results,
    }


async def evaluate_metrics(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    dataset_path: Path,
    on_group_start: Callable[[str], None] | None = None,
    on_group_complete: Callable[[str, object], None] | None = None,
) -> dict[str, object]:
    if not re.fullmatch(r"tracerisk_eval_[0-9a-f]{32}", settings.postgres_db):
        raise ValueError("Metrics require an isolated generated evaluation database")
    snapshot_hash = source_snapshot_hash()
    if on_group_start is not None:
        on_group_start("rules")
    rule_result = evaluate_rule_cases(snapshot_hash)
    if on_group_complete is not None:
        on_group_complete("rules", rule_result)
    if on_group_start is not None:
        on_group_start("retrieval")
    async with sessions.begin() as session:
        principal = User(
            username="evaluation", password_hash="disabled", role="analyst"
        )
        reviewer = User(
            username="evaluation-reviewer", password_hash="disabled", role="reviewer"
        )
        session.add_all([principal, reviewer])
        await session.flush()
        retrieval = await evaluate_retrieval(
            session,
            dataset_path=dataset_path,
            provider=FakeEmbedding(),
            principal_id=principal.id,
        )
    recall_cases = retrieval["cases"]
    assert isinstance(recall_cases, list)
    recall = MetricResult(
        name="macro_mean_recall_at_5",
        formula="sum(per-case Recall@5) / labeled cases",
        numerator=sum(float(case["recall_at_5"]) for case in recall_cases),
        denominator=len(recall_cases),
        sample_ids=tuple(str(case["case_id"]) for case in recall_cases),
        source_snapshot_sha256=snapshot_hash,
        mode="mock",
        limitations=(
            "Fake embeddings and synthetic source candidates; "
            "not semantic-model quality.",
        ),
    )
    if recall.value != retrieval["overall_recall_at_5"]:
        raise ValueError("Recall metric differs from existing macro-mean evaluation")
    retrieval_result = {"metric": recall, "existing_evaluation": retrieval}
    if on_group_complete is not None:
        on_group_complete("retrieval", retrieval_result)
    if on_group_start is not None:
        on_group_start("citations")
    by_key = {scenario.key: scenario for scenario in generate_catalog()}
    scenario = by_key["existing-judicial"]
    async with sessions.begin() as session:
        investigation = await create_investigation(
            session,
            company_id=scenario.company.id,
            created_by=principal.id,
            as_of_date=BASELINE,
        )
        run_id = investigation.current_run_id
    await execute_workflow(settings, sessions, run_id, "start")
    citation_samples: dict[str, bool] = {}
    response_samples: dict[str, bool] = {}
    async with sessions.begin() as session:
        findings = list(
            (
                await session.scalars(
                    select(AIFinding)
                    .where(AIFinding.run_id == run_id)
                    .order_by(AIFinding.finding_key)
                )
            ).all()
        )
        frozen = await load_frozen_retrieval(session, run_id=run_id)
        by_chunk = {(row.risk_type, str(chunk.id)): chunk for chunk, row in frozen}
        for finding in findings:
            draft = RiskFindingDraft.model_validate(
                {
                    "finding_key": finding.finding_key,
                    "risk_type": finding.risk_type,
                    "claim_code": finding.claim_code,
                    "title": finding.title,
                    "summary": finding.summary,
                    "reasoning": finding.reasoning,
                    "evidence_citations": finding.citations,
                    "evidence_sufficiency": finding.evidence_sufficiency,
                    "confidence_level": finding.confidence_level,
                    "review_recommended": finding.review_recommended,
                }
            )
            await validate_findings(
                session,
                run_id=run_id,
                user_id=principal.id,
                findings=StructuredRiskFindings(findings=[draft]),
            )
            response_samples["existing-judicial"] = True
            for index, citation in enumerate(finding.citations, start=1):
                chunk = by_chunk.get((finding.risk_type, str(citation["chunk_id"])))
                citation_samples[f"existing-judicial:{finding.finding_key}:{index}"] = (
                    chunk is not None
                    and str(chunk.evidence_id) == citation["evidence_id"]
                    and chunk.content_hash == citation["content_hash"]
                    and str(citation["quoted_excerpt"]) in chunk.content
                )
        status = await session.scalar(
            select(WorkflowRun.status).where(WorkflowRun.id == run_id)
        )
    if status != "awaiting_review":
        raise ValueError("Expected mandatory review for judicial evaluation run")
    citation_metric = ratio_metric(
        "citation_legality",
        "valid persisted citations / persisted citations",
        citation_samples,
        source_snapshot_sha256=snapshot_hash,
    )
    if on_group_complete is not None:
        on_group_complete("citations", citation_metric)
    if on_group_start is not None:
        on_group_start("coverage")
    for finding in findings:
        await submit_review(
            sessions,
            FindingReview(
                run_id=run_id,
                reviewer_id=reviewer.id,
                idempotency_key=f"evaluation:{finding.finding_key}",
                decision="approve",
                reason="validated synthetic citation",
                ai_finding_id=finding.id,
                finding_version=finding.version,
            ),
        )
    await execute_workflow(settings, sessions, run_id, "resume")
    async with sessions() as session:
        report = await session.scalar(
            select(RiskReport).where(RiskReport.run_id == run_id)
        )
        status = await session.scalar(
            select(WorkflowRun.status).where(WorkflowRun.id == run_id)
        )
    if report is None or status != "completed":
        raise ValueError("Evaluation run did not produce a completed report")
    frozen_citations = tuple(
        FrozenCitation(
            risk_type=row.risk_type,
            chunk_id=str(chunk.id),
            evidence_id=str(chunk.evidence_id),
            content_hash=chunk.content_hash,
            content=chunk.content,
        )
        for chunk, row in frozen
    )
    coverage_samples = final_coverage_samples(
        report.snapshot.get("final_findings"), frozen_citations
    )
    coverage_metric = ratio_metric(
        "evidence_coverage",
        "final findings with valid citations / final findings requiring evidence",
        coverage_samples,
        source_snapshot_sha256=snapshot_hash,
    )
    if on_group_complete is not None:
        on_group_complete("coverage", coverage_metric)
    if on_group_start is not None:
        on_group_start("structured_output")
    clean = by_key["clean-operations"]
    async with sessions.begin() as session:
        investigation = await create_investigation(
            session,
            company_id=clean.company.id,
            created_by=principal.id,
            as_of_date=BASELINE,
        )
        clean_run_id = investigation.current_run_id
    await execute_workflow(settings, sessions, clean_run_id, "start")
    async with sessions() as session:
        await validate_findings(
            session,
            run_id=clean_run_id,
            user_id=principal.id,
            findings=StructuredRiskFindings(findings=[]),
        )
        if await session.scalar(
            select(AIFinding.id).where(AIFinding.run_id == clean_run_id)
        ):
            raise ValueError("Clean Mock scenario unexpectedly emitted findings")
        response_samples["clean-operations"] = True
    structured_metric = ratio_metric(
        "structured_output_success",
        "responses passing Pydantic, citation, and support validation "
        "/ evaluated complete Mock responses",
        response_samples,
        source_snapshot_sha256=snapshot_hash,
    )
    if on_group_complete is not None:
        on_group_complete("structured_output", structured_metric)
    return {
        "rules": rule_result,
        "retrieval": retrieval_result,
        "citation_legality": citation_metric,
        "evidence_coverage": coverage_metric,
        "structured_output": structured_metric,
        "evaluation_run_status": status,
        "negative_response_tests": [
            "wrong_evidence_id",
            "wrong_chunk_id",
            "wrong_content_hash",
            "noncontiguous_excerpt",
            "other_run_chunk",
            "missing_frozen_retrieval",
        ],
    }
