from dataclasses import dataclass
from datetime import date
from uuid import UUID

from tracerisk.rules.common import RuleEvaluation, references_for, select_latest_states

BUSINESS_RULE_CODE = "BUSINESS_ABNORMAL_V1"
BUSINESS_RULE_VERSION = "1"
BUSINESS_RULE_SCORE = 25


@dataclass(frozen=True)
class BusinessFact:
    evidence_id: UUID
    abnormal_record_id: str
    status: str
    observed_at: date
    content_hash: str


def evaluate_business_abnormal(
    facts: list[BusinessFact], *, as_of_date: date
) -> RuleEvaluation:
    if any(fact.status not in {"active", "resolved"} for fact in facts):
        raise ValueError("Unknown business abnormal status")
    latest = select_latest_states(
        facts,
        as_of_date=as_of_date,
        fact_key=lambda fact: fact.abnormal_record_id,
        observed_at=lambda fact: fact.observed_at,
        evidence_id=lambda fact: fact.evidence_id,
    )
    active = tuple(fact for fact in latest if fact.status == "active")
    return RuleEvaluation(
        rule_code=BUSINESS_RULE_CODE,
        rule_version=BUSINESS_RULE_VERSION,
        score=BUSINESS_RULE_SCORE if active else 0,
        parameters={
            "as_of_date": as_of_date.isoformat(),
            "required_status": "active",
            "minimum_records": 1,
            "dedup_key": "abnormal_record_id",
            "state_selection": "latest_on_or_before_as_of_date",
        },
        matched_fact_keys=tuple(fact.abnormal_record_id for fact in active),
        evidence_references=references_for(
            active,
            evidence_id=lambda fact: fact.evidence_id,
            content_hash=lambda fact: fact.content_hash,
        ),
    )
