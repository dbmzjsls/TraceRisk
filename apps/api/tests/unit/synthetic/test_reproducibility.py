import hashlib
import random
from datetime import date, timedelta
from uuid import UUID

from tracerisk.synthetic.generator import BASELINE_DATE, generate_catalog
from tracerisk.synthetic.scenarios import (
    PRESERVED_COMPANY_ID,
    PRESERVED_EVIDENCE_ROWS,
    EvidenceSpec,
)


def all_evidence() -> tuple[EvidenceSpec, ...]:
    return tuple(
        evidence for scenario in generate_catalog() for evidence in scenario.evidence
    )


def test_catalog_has_ten_ordered_scenarios_and_preserves_existing_judicial_story() -> (
    None
):
    catalog = generate_catalog()

    assert tuple(item.key for item in catalog) == (
        "existing-judicial",
        "clean-operations",
        "all-rules-95",
        "alias-homonym",
        "duplicate-news",
        "cross-source-conflict",
        "unavailable-source",
        "news-window-boundaries",
        "business-date-boundaries",
        "prompt-injection",
    )
    existing = catalog[0]
    assert existing.company.id == PRESERVED_COMPANY_ID
    assert existing.company.synthetic_key == "judicial-open-case-story"
    assert existing.company.name == "华辰智造（合成演示）有限公司"
    assert existing.company.unified_credit_code == "SYNTHETIC-COMPANY-001"
    assert existing.company.is_synthetic is True
    assert all(
        item.source_type == "judicial"
        and item.risk_type == "judicial"
        and item.fact_type == "judicial_case_status"
        and item.fact_value == "open"
        and item.polarity == "negative"
        for item in existing.evidence
    )
    assert (
        tuple(
            (
                item.id,
                item.source_uri,
                item.title,
                item.observed_at,
                item.fact_key,
                item.content,
            )
            for item in existing.evidence
        )
        == PRESERVED_EVIDENCE_ROWS
    )


def test_catalog_has_unique_ids_hashes_and_complete_source_coverage() -> None:
    catalog = generate_catalog()
    companies = tuple(item.company for item in catalog)
    aliases = tuple(alias for item in catalog for alias in item.aliases)
    evidence = all_evidence()
    coverage = tuple(item for scenario in catalog for item in scenario.coverage)

    assert len({item.id for item in companies}) == len(companies) == 10
    assert len({item.id for item in aliases}) == len(aliases)
    assert len({item.id for item in evidence}) == len(evidence)
    assert len({item.id for item in coverage}) == len(coverage)
    all_ids = tuple(item.id for item in companies + aliases + evidence + coverage)
    assert len(set(all_ids)) == len(all_ids)
    assert {item.source_type for item in coverage} == {
        "business",
        "judicial",
        "news",
        "intellectual_property",
    }
    assert {item.status for item in coverage} == {"available", "unavailable", "stale"}
    assert all(
        {item.source_type for item in scenario.coverage}
        == {"business", "judicial", "news", "intellectual_property"}
        for scenario in catalog
    )
    assert all(
        item.content_hash == hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        for item in evidence
    )
    assert all(
        alias.company_id == scenario.company.id
        for scenario in catalog
        for alias in scenario.aliases
    )


def test_catalog_encodes_structured_edge_cases_without_parsing_prose() -> None:
    catalog = {item.key: item for item in generate_catalog()}

    alias_company = catalog["alias-homonym"]
    homonym_company = catalog["duplicate-news"]
    assert alias_company.company.name == homonym_company.company.name
    assert alias_company.company.id != homonym_company.company.id

    all_rules = catalog["all-rules-95"].evidence
    assert catalog["all-rules-95"].expectation.requires_review is True
    assert {item.fact_type for item in all_rules if "ip_status" in item.fact_type} == {
        "core_ip_status",
        "noncore_ip_status",
    }

    duplicate_news = catalog["duplicate-news"].evidence
    duplicate_story_ids = [
        item.fact_key for item in duplicate_news if item.risk_type == "news"
    ]
    assert duplicate_story_ids.count("STORY-DUP-001") == 2

    conflict = catalog["cross-source-conflict"].evidence
    conflict_values = {
        (item.source_type, item.fact_value)
        for item in conflict
        if item.fact_type == "judicial_case_status"
        and item.fact_key == "CONFLICT-CASE-001"
    }
    assert conflict_values == {("judicial", "open"), ("business", "closed")}
    assert {item.status for item in catalog["cross-source-conflict"].coverage} == {
        "available"
    }
    assert {item.status for item in catalog["unavailable-source"].coverage} == {
        "available",
        "unavailable",
        "stale",
    }

    news_dates = {
        item.observed_at
        for item in catalog["news-window-boundaries"].evidence
        if item.risk_type == "news"
    }
    assert news_dates == {
        BASELINE_DATE - timedelta(days=365),
        BASELINE_DATE - timedelta(days=364),
        BASELINE_DATE,
        BASELINE_DATE + timedelta(days=1),
    }

    business = catalog["business-date-boundaries"].evidence
    assert {
        (item.fact_key, item.fact_value, item.observed_at)
        for item in business
        if item.fact_type == "business_abnormal_status"
    } >= {
        ("ABN-POST-RESOLUTION", "active", date(2026, 9, 10)),
        ("ABN-POST-RESOLUTION", "resolved", date(2026, 9, 12)),
        ("ABN-ON-BASELINE", "resolved", BASELINE_DATE),
        ("ABN-FUTURE", "active", date(2026, 9, 12)),
    }

    marker = "忽略此前规则"
    injection = catalog["prompt-injection"].evidence[0]
    assert marker in injection.content
    non_content_fields = tuple(
        value
        for scenario in catalog.values()
        for value in (
            scenario.key,
            scenario.description,
            scenario.company.synthetic_key,
            scenario.company.name,
            scenario.company.unified_credit_code,
            *scenario.tags,
            *(alias.name for alias in scenario.aliases),
            *(
                value
                for evidence in scenario.evidence
                for value in (
                    evidence.source_type,
                    evidence.risk_type,
                    evidence.source_uri,
                    evidence.title,
                    evidence.fact_type,
                    evidence.fact_key,
                    evidence.fact_value,
                    evidence.polarity,
                    evidence.content_hash,
                )
            ),
            *(
                value
                for coverage in scenario.coverage
                for value in (coverage.source_type, coverage.status)
            ),
        )
    )
    assert all(marker not in value for value in non_content_fields)
    assert [
        evidence.content
        for scenario in catalog.values()
        for evidence in scenario.evidence
        if marker in evidence.content
    ] == [injection.content]


def test_catalog_is_reproducible_and_independent_from_global_random() -> None:
    first = generate_catalog()
    random.seed(9472)
    for _ in range(100):
        random.random()
    second = generate_catalog()

    assert first == second
    assert generate_catalog(seed=20260915) == generate_catalog(seed=20260915)
    assert all(
        item.as_of_date == BASELINE_DATE
        for scenario in first
        for item in scenario.coverage
    )
    assert all(isinstance(item.id, UUID) for item in all_evidence())
