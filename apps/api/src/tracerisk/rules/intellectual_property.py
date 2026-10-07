from dataclasses import dataclass
from datetime import date
from uuid import UUID

from tracerisk.rules.common import RuleEvaluation, references_for, select_latest_states

CORE_IP_RULE_CODE = "CORE_IP_ANOMALY_V1"
CORE_IP_RULE_VERSION = "1"
CORE_IP_RULE_SCORE = 15


@dataclass(frozen=True)
class IntellectualPropertyFact:
    evidence_id: UUID
    ip_record_id: str
    status: str
    fact_type: str
    observed_at: date
    content_hash: str


def evaluate_core_ip_anomaly(
    facts: list[IntellectualPropertyFact], *, as_of_date: date
) -> RuleEvaluation:
    if any(
        fact.fact_type not in {"core_ip_status", "noncore_ip_status"}
        or fact.status not in {"valid", "invalid", "ownership_dispute"}
        for fact in facts
    ):
        raise ValueError("Unknown intellectual-property fact type or status")
    core_facts = [fact for fact in facts if fact.fact_type == "core_ip_status"]
    latest = select_latest_states(
        core_facts,
        as_of_date=as_of_date,
        fact_key=lambda fact: fact.ip_record_id,
        observed_at=lambda fact: fact.observed_at,
        evidence_id=lambda fact: fact.evidence_id,
    )
    anomalies = tuple(
        fact for fact in latest if fact.status in {"invalid", "ownership_dispute"}
    )
    return RuleEvaluation(
        rule_code=CORE_IP_RULE_CODE,
        rule_version=CORE_IP_RULE_VERSION,
        score=CORE_IP_RULE_SCORE if anomalies else 0,
        parameters={
            "as_of_date": as_of_date.isoformat(),
            "fact_type": "core_ip_status",
            "anomalous_statuses": ["invalid", "ownership_dispute"],
            "minimum_records": 1,
            "dedup_key": "ip_record_id",
            "state_selection": "latest_on_or_before_as_of_date",
        },
        matched_fact_keys=tuple(fact.ip_record_id for fact in anomalies),
        evidence_references=references_for(
            anomalies,
            evidence_id=lambda fact: fact.evidence_id,
            content_hash=lambda fact: fact.content_hash,
        ),
    )
