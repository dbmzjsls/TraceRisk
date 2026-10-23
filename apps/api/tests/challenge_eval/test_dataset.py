import json
import re
from collections import Counter
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from pydantic import ValidationError

from tracerisk.ai.claims import CLAIM_CATALOG, ClaimFact, decide_claim_support
from tracerisk.challenge_eval.dataset import load_cases, validate_frozen_shape
from tracerisk.challenge_eval.schemas import (
    AttemptRecord,
    CaseResult,
    ChallengeCase,
    ChallengeChunk,
    ReviewRecord,
    RunManifest,
    RunSummary,
)

DATASET = Path(__file__).resolve().parents[4] / (
    "datasets/evaluation/model_challenge_cases.jsonl"
)
CONFIG = DATASET.with_name("model_challenge_config.json")


def same_observation_conflicts(
    case: ChallengeCase,
) -> list[tuple[ChallengeChunk, ChallengeChunk]]:
    """Different dates describe status changes, not this dataset's conflict gold."""
    exclusive_values = {
        "judicial_case_status": {"open", "closed"},
        "business_abnormal_status": {"active", "resolved"},
        "negative_news_story": {"negative", "positive"},
        "core_ip_status": {"valid", "invalid", "ownership_dispute"},
    }
    return [
        (left, right)
        for left, right in combinations(case.chunks, 2)
        if left.company_id == right.company_id == case.company_id
        and left.risk_type == right.risk_type
        and left.fact_type == right.fact_type
        and left.fact_key == right.fact_key
        and left.observed_at == right.observed_at
        and left.source_type != right.source_type
        and left.fact_value != right.fact_value
        and {left.fact_value, right.fact_value}
        <= exclusive_values.get(left.fact_type, set())
    ]


def claim_fact(chunk: ChallengeChunk) -> ClaimFact:
    return ClaimFact(
        risk_type=chunk.risk_type,
        source_type=chunk.source_type,
        fact_type=chunk.fact_type,
        fact_key=chunk.fact_key,
        fact_value=chunk.fact_value,
        polarity=chunk.polarity,
    )


def write_cases(path: Path, cases: list[ChallengeCase]) -> Path:
    path.write_text(
        "\n".join(case.model_dump_json() for case in cases) + "\n", encoding="utf-8"
    )
    return path


def test_load_exact_byte_hash(tmp_path: Path, valid_case: ChallengeCase) -> None:
    path = write_cases(tmp_path / "cases.jsonl", [valid_case])
    path.write_bytes(b"\n" + path.read_bytes() + b"\n")
    loaded = load_cases(path)
    assert loaded.cases == [valid_case]
    assert loaded.sha256 == sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_case", "duplicate case_id"),
        ("duplicate_chunk", "duplicate chunk_id"),
        ("wrong_hash", "content_hash"),
        ("missing_support", "supporting_chunk_ids"),
        ("missing_distractor", "distractor_chunk_ids"),
        ("overlap", "overlap"),
        ("unknown_claim", "claim code"),
    ],
)
def test_reject_integrity_errors(
    tmp_path: Path, valid_case: ChallengeCase, mutation: str, message: str
) -> None:
    case = valid_case.model_copy(deep=True)
    cases = [case]
    if mutation == "duplicate_case":
        cases.append(case)
    elif mutation == "duplicate_chunk":
        case.chunks.append(case.chunks[0])
    elif mutation == "wrong_hash":
        case.chunks[0].content_hash = "0" * 64
    elif mutation == "missing_support":
        case.supporting_chunk_ids = [UUID(int=99)]
    elif mutation == "missing_distractor":
        case.distractor_chunk_ids = [UUID(int=99)]
    elif mutation == "overlap":
        case.distractor_chunk_ids = case.supporting_chunk_ids.copy()
    else:
        case.allowed_claim_codes = ["UNSUPPORTED"]
    with pytest.raises(ValueError, match=message):
        load_cases(write_cases(tmp_path / "cases.jsonl", cases))


def test_chunk_ids_are_unique_across_cases(
    tmp_path: Path, valid_case: ChallengeCase
) -> None:
    other = valid_case.model_copy(update={"case_id": "other"})
    with pytest.raises(ValueError, match="duplicate chunk_id"):
        load_cases(write_cases(tmp_path / "cases.jsonl", [valid_case, other]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rationale", "  "),
        ("expected_action", "skip"),
        ("category", "other"),
        ("extra", True),
    ],
)
def test_reject_invalid_case_schema(
    tmp_path: Path, valid_case: ChallengeCase, field: str, value: object
) -> None:
    data = valid_case.model_dump(mode="json")
    data[field] = value
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_cases(path)


def test_reject_category_imbalance(tmp_path: Path, valid_case: ChallengeCase) -> None:
    loaded = load_cases(write_cases(tmp_path / "cases.jsonl", [valid_case]))
    with pytest.raises(ValueError, match="40 cases.*10 per category"):
        validate_frozen_shape(loaded)


def test_human_metrics_remain_unmeasured() -> None:
    result = CaseResult(
        case_id="explicit-001",
        expected_action="accept",
        actual_action=None,
        first_attempt_usable=False,
        attempts=[],
        failure_localization_ms=None,
        failure_localization_status="interrupted_before_finalize",
        review_measurement_status="not_measured",
    )
    assert result.human_modified_field_count is None
    assert result.review_duration_ms is None


@pytest.mark.parametrize("case_id", ["", "   ", "x" * 257])
def test_case_result_rejects_invalid_case_ids(case_id: str) -> None:
    with pytest.raises(ValidationError, match="case_id"):
        CaseResult(
            case_id=case_id,
            expected_action="accept",
            actual_action=None,
            first_attempt_usable=False,
            attempts=[],
            failure_localization_ms=None,
            failure_localization_status="interrupted_before_finalize",
            review_measurement_status="not_measured",
        )


@pytest.mark.parametrize(
    ("field", "key"),
    [("action_counts", "accept"), ("attribution_counts", "structure_error")],
)
def test_summary_rejects_negative_mapping_counts(field: str, key: str) -> None:
    data: dict[str, object] = {
        "run_id": "run-001",
        "case_count": 0,
        "first_attempt_usable_count": 0,
        "action_counts": {},
        "attribution_counts": {},
    }
    data[field] = {key: -1}
    with pytest.raises(ValidationError, match=field):
        RunSummary.model_validate(data)


@pytest.mark.parametrize(
    "model",
    [
        ChallengeCase,
        ChallengeChunk,
        AttemptRecord,
        CaseResult,
        ReviewRecord,
        RunManifest,
        RunSummary,
    ],
)
def test_all_artifacts_forbid_extra_fields(model: type[CaseResult]) -> None:
    assert model.model_config["extra"] == "forbid"


def test_missing_api_metrics_remain_null() -> None:
    attempt = AttemptRecord(
        case_id="explicit-001",
        attempt_number=1,
        request_kind="original",
        latency_ms=1,
        status="succeeded",
        retryable=False,
        requested_model="model",
    )
    assert attempt.input_tokens is None
    assert attempt.output_tokens is None
    assert attempt.cache_hit_tokens is None
    assert attempt.cache_miss_tokens is None


def test_same_count_with_wrong_categories_is_rejected(
    tmp_path: Path, valid_case: ChallengeCase
) -> None:
    loaded = load_cases(write_cases(tmp_path / "cases.jsonl", [valid_case]))
    loaded.cases = [valid_case] * 40
    with pytest.raises(ValueError, match="40 cases.*10 per category"):
        validate_frozen_shape(loaded)


def test_frozen_dataset_is_balanced_and_valid() -> None:
    loaded = load_cases(DATASET)
    validate_frozen_shape(loaded)
    assert len(loaded.cases) == 40
    assert Counter(case.category for case in loaded.cases) == {
        "explicit_evidence": 10,
        "similar_entity_or_number": 10,
        "source_conflict": 10,
        "insufficient_evidence": 10,
    }
    assert loaded.sha256 == sha256(DATASET.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "category",
    [
        "explicit_evidence",
        "similar_entity_or_number",
        "source_conflict",
        "insufficient_evidence",
    ],
)
def test_each_category_covers_every_risk_type_twice(category: str) -> None:
    counts = Counter(
        risk
        for case in load_cases(DATASET).cases
        if case.category == category
        for risk in {CLAIM_CATALOG[code].risk_type for code in case.allowed_claim_codes}
    )
    assert set(counts) == {
        definition.risk_type for definition in CLAIM_CATALOG.values()
    }
    assert all(count >= 2 for count in counts.values())


def test_frozen_case_semantics() -> None:
    for case in load_cases(DATASET).cases:
        assert re.search(r"[\u4e00-\u9fff]", case.rationale), case.case_id
        chunks = {chunk.chunk_id: chunk for chunk in case.chunks}
        for code in case.allowed_claim_codes:
            assert any(
                chunk.risk_type == CLAIM_CATALOG[code].risk_type
                for chunk in case.chunks
            )
        if case.category == "explicit_evidence":
            assert case.expected_action == "accept"
            assert len(case.supporting_chunk_ids) == 1
            support = chunks[case.supporting_chunk_ids[0]]
            assert support.company_id == case.company_id
            facts = [claim_fact(support)]
            assert all(
                decide_claim_support(code, support.risk_type, facts).status
                == "supported"
                for code in case.allowed_claim_codes
            )
        elif case.category == "similar_entity_or_number":
            assert case.distractor_chunk_ids, case.case_id
            assert case.expected_action in {"accept", "reject", "manual_review"}
        elif case.category == "source_conflict":
            assert case.expected_action == "manual_review"
            conflicts = same_observation_conflicts(case)
            assert conflicts, case.case_id
            assert any(
                {left.chunk_id, right.chunk_id} <= set(case.supporting_chunk_ids)
                for left, right in conflicts
            ), case.case_id
        else:
            assert case.expected_action in {"reject", "manual_review"}


def test_gold_accepts_and_rejects_follow_visible_target_facts() -> None:
    for case in load_cases(DATASET).cases:
        if case.expected_action == "manual_review":
            continue
        supported = {
            chunk.chunk_id
            for chunk in case.chunks
            if chunk.company_id == case.company_id
            and any(
                decide_claim_support(code, chunk.risk_type, [claim_fact(chunk)]).status
                == "supported"
                for code in case.allowed_claim_codes
            )
        }
        assert set(case.supporting_chunk_ids) == supported, case.case_id
        assert bool(supported) == (case.expected_action == "accept"), case.case_id


def test_frozen_ids_and_hashes_use_deterministic_content() -> None:
    for case in load_cases(DATASET).cases:
        for ordinal, chunk in enumerate(case.chunks, start=1):
            for field, kind in (("chunk_id", "chunk"), ("evidence_id", "evidence")):
                assert getattr(chunk, field) == uuid5(
                    NAMESPACE_URL,
                    f"tracerisk-challenge/{case.case_id}/{kind}/{ordinal}",
                )
            assert chunk.evidence_content_hash == chunk.content_hash


@pytest.fixture
def status_change_case(valid_case: ChallengeCase) -> ChallengeCase:
    earlier = valid_case.chunks[0].model_copy(
        update={"observed_at": valid_case.chunks[0].observed_at.replace(day=22)}
    )
    content = "The previously open case is now closed."
    digest = sha256(content.encode("utf-8")).hexdigest()
    later = valid_case.chunks[0].model_copy(
        update={
            "source_type": "news",
            "fact_value": "closed",
            "chunk_id": UUID(int=4),
            "evidence_id": UUID(int=5),
            "polarity": "positive",
            "content": content,
            "content_hash": digest,
            "evidence_content_hash": digest,
        }
    )
    return valid_case.model_copy(update={"chunks": [earlier, later]})


def test_different_observation_dates_are_not_a_conflict(
    status_change_case: ChallengeCase,
) -> None:
    assert not same_observation_conflicts(status_change_case)
    status_change_case.chunks[0].observed_at = status_change_case.chunks[1].observed_at
    assert same_observation_conflicts(status_change_case)


def test_frozen_model_and_pricing_configuration() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert {
        key: config[key]
        for key in (
            "provider",
            "base_url",
            "model",
            "thinking",
            "temperature",
            "max_tokens",
            "response_format",
            "max_attempts",
            "pricing_snapshot_date",
            "pricing_currency",
            "price_per_million_tokens",
        )
    } == {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "max_attempts": 2,
        "pricing_snapshot_date": "2026-10-23",
        "pricing_currency": "USD",
        "price_per_million_tokens": {
            "peak_cache_hit_input": 0.006,
            "peak_cache_miss_input": 0.3,
            "peak_output": 1.2,
            "off_peak_cache_hit_input": 0.003,
            "off_peak_cache_miss_input": 0.15,
            "off_peak_output": 0.6,
        },
    }
    assert config["pricing_url"] == "https://api-docs.deepseek.com/quick_start/pricing/"
    assert config["pricing_policy"] == {
        "timezone": "UTC",
        "peak_weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "peak_intervals": [["01:00", "04:00"], ["06:00", "10:00"]],
        "interval_bounds": "start_inclusive_end_exclusive",
        "exclude_chinese_public_holidays": True,
        "holiday_calendar": "checked_in_calendar_required",
        "unknown_holiday_pricing_tier": "peak",
        "cost_field": "conservative_estimated_cost_usd",
        "tier_timestamp": "actual_request_timestamp",
        "manifest_tier_field": "pricing_tier",
    }
