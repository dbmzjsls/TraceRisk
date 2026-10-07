from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from tracerisk.rules.common import RuleEvaluation, references_for, select_latest_states

NEWS_RULE_CODE = "NEGATIVE_NEWS_V1"
NEWS_RULE_VERSION = "1"
NEWS_RULE_SCORE = 20


@dataclass(frozen=True)
class NewsFact:
    evidence_id: UUID
    canonical_story_id: str
    polarity: str
    observed_at: date
    content_hash: str


def evaluate_negative_news(
    facts: list[NewsFact], *, as_of_date: date
) -> RuleEvaluation:
    if any(fact.polarity not in {"positive", "negative", "neutral"} for fact in facts):
        raise ValueError("Unknown news polarity")
    window_start = as_of_date - timedelta(days=364)
    negative = [
        fact
        for fact in facts
        if window_start <= fact.observed_at <= as_of_date
        and fact.polarity == "negative"
    ]
    stories = select_latest_states(
        negative,
        as_of_date=as_of_date,
        fact_key=lambda fact: fact.canonical_story_id,
        observed_at=lambda fact: fact.observed_at,
        evidence_id=lambda fact: fact.evidence_id,
    )
    matched = stories if len(stories) >= 2 else ()
    return RuleEvaluation(
        rule_code=NEWS_RULE_CODE,
        rule_version=NEWS_RULE_VERSION,
        score=NEWS_RULE_SCORE if len(stories) >= 2 else 0,
        parameters={
            "as_of_date": as_of_date.isoformat(),
            "window_start": window_start.isoformat(),
            "window_days": 365,
            "required_polarity": "negative",
            "minimum_stories": 2,
            "dedup_key": "canonical_story_id",
        },
        matched_fact_keys=tuple(fact.canonical_story_id for fact in matched),
        evidence_references=references_for(
            matched,
            evidence_id=lambda fact: fact.evidence_id,
            content_hash=lambda fact: fact.content_hash,
        ),
    )
