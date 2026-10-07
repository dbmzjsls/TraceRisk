"""Immutable business claim vocabulary and deterministic, IO-free support decisions."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from tracerisk.ai.schemas import RiskType

SupportStatus = Literal["supported", "contradicted", "indeterminate"]


@dataclass(frozen=True)
class ClaimDefinition:
    risk_type: RiskType
    fact_type: str
    fact_values: frozenset[str]
    polarity: Literal["negative"] = "negative"


CLAIM_CATALOG: Mapping[str, ClaimDefinition] = MappingProxyType(
    {
        "JUDICIAL_OPEN_CASE": ClaimDefinition(
            "judicial", "judicial_case_status", frozenset({"open"})
        ),
        "BUSINESS_ABNORMAL": ClaimDefinition(
            "business", "business_abnormal_status", frozenset({"active"})
        ),
        "NEGATIVE_NEWS": ClaimDefinition(
            "news", "negative_news_story", frozenset({"negative"})
        ),
        "CORE_IP_ANOMALY": ClaimDefinition(
            "intellectual_property",
            "core_ip_status",
            frozenset({"invalid", "ownership_dispute"}),
        ),
    }
)


@dataclass(frozen=True)
class ClaimFact:
    risk_type: str
    source_type: str
    fact_type: str
    fact_key: str
    fact_value: str
    polarity: str


@dataclass(frozen=True)
class SupportDecision:
    status: SupportStatus
    reason: str


class UnsupportedClaimError(ValueError):
    """The supplied claim is outside the explicitly supported catalog."""


def canonical_statement(fact: ClaimFact) -> str:
    """Exact factual wording; quote keys so their text is never connective prose."""
    key = json.dumps(fact.fact_key, ensure_ascii=False)
    return (
        f"{fact.risk_type}: {fact.fact_type}[{key}] = "
        f"{fact.fact_value} ({fact.polarity})."
    )


def structured_statement(fact: ClaimFact) -> str:
    """A fixed, verbatim combination of structured fields is also canonical."""
    return json.dumps(
        {
            "risk_type": fact.risk_type,
            "fact_type": fact.fact_type,
            "fact_key": fact.fact_key,
            "fact_value": fact.fact_value,
            "polarity": fact.polarity,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def decide_claim_support(
    claim_code: str, risk_type: str, facts: Sequence[ClaimFact]
) -> SupportDecision:
    definition = CLAIM_CATALOG.get(claim_code)
    if definition is None:
        raise UnsupportedClaimError("Unsupported claim code")
    if risk_type != definition.risk_type:
        return SupportDecision("contradicted", "claim_domain_mismatch")
    if not facts:
        return SupportDecision("indeterminate", "no_supporting_citation")
    for fact in facts:
        if (
            fact.risk_type != definition.risk_type
            or fact.source_type != definition.risk_type
            or fact.fact_type != definition.fact_type
            or fact.fact_value not in definition.fact_values
            or fact.polarity != definition.polarity
        ):
            return SupportDecision("contradicted", "cited_fact_mismatch")
    return SupportDecision("supported", "canonical_fact_match")
