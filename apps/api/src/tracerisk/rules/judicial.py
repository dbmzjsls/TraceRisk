from dataclasses import dataclass
from datetime import date
from uuid import UUID

from tracerisk.rules.common import EvidenceReference as EvidenceReference
from tracerisk.rules.common import RuleEvaluation, references_for, select_latest_states

JUDICIAL_RULE_CODE = "JUDICIAL_OPEN_CASE_V1"
JUDICIAL_RULE_VERSION = "1"
JUDICIAL_RULE_SCORE = 35


@dataclass(frozen=True)
class JudicialFact:
    evidence_id: UUID
    case_number: str
    status: str
    observed_at: date
    content_hash: str


def evaluate_judicial_open_cases(
    facts: list[JudicialFact], *, as_of_date: date
) -> RuleEvaluation:
    if any(fact.status not in {"open", "closed"} for fact in facts):
        raise ValueError("Unknown judicial case status")
    latest = select_latest_states(
        facts,
        as_of_date=as_of_date,
        fact_key=lambda fact: fact.case_number,
        observed_at=lambda fact: fact.observed_at,
        evidence_id=lambda fact: fact.evidence_id,
    )
    open_facts = tuple(fact for fact in latest if fact.status == "open")
    return RuleEvaluation(
        rule_code=JUDICIAL_RULE_CODE,
        rule_version=JUDICIAL_RULE_VERSION,
        score=JUDICIAL_RULE_SCORE if open_facts else 0,
        parameters={
            "as_of_date": as_of_date.isoformat(),
            "required_status": "open",
            "minimum_cases": 1,
            "dedup_key": "case_number",
            "state_selection": "latest_on_or_before_as_of_date",
        },
        matched_fact_keys=tuple(fact.case_number for fact in open_facts),
        evidence_references=references_for(
            open_facts,
            evidence_id=lambda fact: fact.evidence_id,
            content_hash=lambda fact: fact.content_hash,
        ),
    )
