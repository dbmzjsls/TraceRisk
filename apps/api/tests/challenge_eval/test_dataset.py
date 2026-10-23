import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

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


@pytest.mark.xfail(not DATASET.exists(), reason="Task 2 supplies the frozen dataset")
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
