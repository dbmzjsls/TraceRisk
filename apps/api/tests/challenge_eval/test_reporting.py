import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracerisk.challenge_eval import reporting, runner
from tracerisk.challenge_eval.attribution import select_improvement
from tracerisk.challenge_eval.schemas import AttemptRecord, RunManifest
from tracerisk.challenge_eval.scoring import ScoredCase, score_case
from tracerisk.challenge_eval.validation import ValidationOutcome

CONFIG = {
    "impact_weights": {
        "unsafe_accept_or_missed_review": 4,
        "wrong_reject": 3,
        "unnecessary_review": 2,
        "format_retry_only": 1,
    },
    "price_per_million_tokens": {
        "peak_cache_hit_input": 0.006,
        "peak_cache_miss_input": 0.3,
        "peak_output": 1.2,
        "off_peak_cache_hit_input": 0.003,
        "off_peak_cache_miss_input": 0.15,
        "off_peak_output": 0.6,
    },
}
SECRET = "PRIVATE_SAFE_RESPONSE_918273"
ABSENT_SENTINEL = "8675309"


def attempt(
    case_id: str,
    *,
    fingerprint: str | None = "fp-a",
    returned_model: str | None = "deepseek-flash-20261017",
) -> AttemptRecord:
    return AttemptRecord(
        case_id=case_id,
        attempt_number=1,
        request_kind="original",
        requested_at=datetime(2026, 10, 23, tzinfo=UTC),
        pricing_tier="peak",
        latency_ms=10,
        status="succeeded",
        retryable=False,
        requested_model="deepseek-flash",
        returned_model=returned_model,
        system_fingerprint=fingerprint,
        response_content=SECRET,
        input_tokens=10,
        output_tokens=5,
        cache_hit_tokens=4,
        cache_miss_tokens=6,
        total_tokens=15,
    )


def scored(valid_case, case_id: str, expected: str, actual: str) -> ScoredCase:
    case = valid_case.model_copy(
        update={"case_id": case_id, "expected_action": expected}
    )
    record = attempt(case_id)
    return score_case(
        case,
        [record],
        [ValidationOutcome(actual_action=actual)],
        CONFIG,
        failure_localization_ms=7,
    )


def scored_after_repair(valid_case, case_id: str) -> ScoredCase:
    case = valid_case.model_copy(
        update={"case_id": case_id, "expected_action": "accept"}
    )
    first = attempt(case_id)
    second = first.model_copy(
        update={"attempt_number": 2, "request_kind": "validation_repair"}
    )
    return score_case(
        case,
        [first, second],
        [
            ValidationOutcome(actual_action="reject", error_type="structure_error"),
            ValidationOutcome(actual_action="accept"),
        ],
        CONFIG,
        failure_localization_ms=7,
    )


def raw_run(
    results: list[ScoredCase],
    *,
    review_case_ids: list[str] | None = None,
    fingerprint: str | None = "fp-a",
    returned_model: str | None = "deepseek-flash-20261017",
):
    attempts = [
        stored.model_copy(
            update={
                "system_fingerprint": fingerprint,
                "returned_model": returned_model,
                "response_content": SECRET,
            }
        )
        for item in results
        for stored in item.attempts
    ]
    raw = {
        "manifest.json": b'{"manifest":"validated"}\n',
        "dataset.jsonl": b'{"dataset":"synthetic"}\n',
        "config.json": json.dumps(CONFIG, sort_keys=True).encode(),
        "attempts.jsonl": b"".join(
            runner.canonical(item.model_dump(mode="json")) + b"\n" for item in attempts
        ),
        "case-events.jsonl": b"".join(
            runner.canonical({"case_id": item.case_id}) + b"\n" for item in results
        ),
        "cases.jsonl": b"".join(
            runner.canonical(item.model_dump(mode="json")) + b"\n" for item in results
        ),
    }
    if review_case_ids is not None:
        raw["reviews.jsonl"] = b"".join(
            runner.canonical(
                {
                    "case_id": case_id,
                    "decision": "manual_review",
                    "changed_fields": ["title"],
                    "changed_field_count": 1,
                    "review_duration_seconds": 1.25,
                    "validation_status": "valid",
                }
            )
            + b"\n"
            for case_id in review_case_ids
        )
    return raw


def manifest(phase: str, *, fingerprint: str = "fp-a") -> RunManifest:
    return RunManifest(
        run_id=phase,
        phase=phase,
        created_at=datetime(2026, 10, 23, tzinfo=UTC),
        dataset_hash="1" * 64,
        config_hash="2" * 64,
        lock_hash="3" * 64,
        prompt_hash="4" * 64,
        fingerprint="5" * 64,
        requested_model="deepseek-flash",
        status="completed",
        environment={"python": "3.12.12", "os": "Windows-test", "local": True},
        source_inventory={"validation.py": "6" * 64},
        behavior_inventory={"validation.py": {"1:route_review": "7" * 64}},
        changed_dimension="review_routing" if phase == "improved" else None,
    )


def install_runs(monkeypatch, baseline, improved, left_raw, right_raw):
    def selected(path: Path):
        return left_raw if path.name == "baseline" else right_raw

    def fake(path: Path, **kwargs):
        if path.name == "baseline":
            return manifest("baseline"), baseline, left_raw
        return manifest("improved"), improved, right_raw

    monkeypatch.setattr(reporting, "snapshot_run_inputs", selected)
    monkeypatch.setattr(reporting, "recompute_run", fake)
    monkeypatch.setattr(reporting, "verify_improvement", lambda *args, **kwargs: None)


def test_reports_derive_metrics_and_render_unmeasured_human_work(
    tmp_path, monkeypatch, valid_case
):
    baseline = [
        scored(valid_case, "success-001", "accept", "accept"),
        scored(valid_case, "failure-001", "accept", "reject"),
        scored(valid_case, "review-001", "manual_review", "manual_review"),
    ]
    improved = [
        scored(valid_case, "success-001", "accept", "accept"),
        scored(valid_case, "failure-001", "accept", "accept"),
        scored(valid_case, "review-001", "manual_review", "manual_review"),
    ]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))

    summary = reporting.generate_reports(
        tmp_path / "baseline",
        tmp_path / "improved",
        analysis_path,
        tmp_path / "reports",
    )

    assert summary.baseline.http_attempt_count == 3
    assert summary.baseline.input_tokens == 30
    assert summary.baseline.action_counts == {
        "accept": 1,
        "reject": 1,
        "manual_review": 1,
    }
    assert summary.baseline.total_human_modified_field_count is None
    assert summary.delta.selected_problem_case_count == -1
    comparison = (tmp_path / "reports/comparison.md").read_text(encoding="utf-8")
    assert "not measured" in comparison
    assert "| 总 Token | 45 | 45 |" in comparison
    assert "| 通过率 |" not in comparison
    assert "本地非生产环境" in comparison
    assert (tmp_path / "baseline/summary.json").is_file()
    assert (tmp_path / "improved/summary.json").is_file()


def test_reports_are_safe_and_include_deterministic_typical_cases(
    tmp_path, monkeypatch, valid_case
):
    baseline = [
        scored(valid_case, "z-success", "accept", "accept"),
        scored(valid_case, "a-success", "reject", "reject"),
        scored(valid_case, "b-failure", "accept", "reject"),
        scored(valid_case, "a-failure", "manual_review", "accept"),
        scored(valid_case, "review-only", "manual_review", "manual_review"),
    ]
    improved = [item.model_copy() for item in baseline]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))

    reporting.generate_reports(
        tmp_path / "baseline",
        tmp_path / "improved",
        analysis_path,
        tmp_path / "reports",
    )
    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "reports").iterdir()
        if path.is_file()
    )
    typical = (tmp_path / "reports/typical-cases.md").read_text(encoding="utf-8")
    resume = (tmp_path / "reports/resume-bullet.md").read_text(encoding="utf-8")
    assert "a-success" in typical
    assert "a-failure" in typical
    assert "review-only" in typical
    assert "发现问题—完成改进—过程指标变化" in resume
    assert "实验结论" in resume
    assert SECRET not in rendered
    assert ABSENT_SENTINEL not in rendered
    assert SECRET not in (tmp_path / "baseline/summary.json").read_text()
    assert SECRET not in (tmp_path / "improved/summary.json").read_text()


def test_completed_reviews_are_the_only_human_metric_source(
    tmp_path, monkeypatch, valid_case
):
    baseline = [scored(valid_case, "review-001", "manual_review", "manual_review")]
    improved = [item.model_copy() for item in baseline]
    left_raw = raw_run(baseline, review_case_ids=["review-001"])
    right_raw = raw_run(improved, review_case_ids=["review-001"])
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    summary = reporting.build_comparison(
        tmp_path / "baseline", tmp_path / "improved", analysis_path
    )
    assert summary.baseline.reviewed_case_count == 1
    assert summary.baseline.total_human_modified_field_count == 1
    assert summary.baseline.total_review_duration_ms == 1250


def test_asymmetric_review_provenance_is_rejected(tmp_path, monkeypatch, valid_case):
    baseline = [scored(valid_case, "review-001", "manual_review", "manual_review")]
    improved = [item.model_copy() for item in baseline]
    left_raw = raw_run(baseline, review_case_ids=["review-001"])
    right_raw = raw_run(improved)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    with pytest.raises(ValueError, match="review provenance"):
        reporting.build_comparison(
            tmp_path / "baseline", tmp_path / "improved", analysis_path
        )


def test_compare_cli_invokes_reporter_without_api_key(
    tmp_path, monkeypatch, valid_case
):
    baseline = [scored(valid_case, "failure-001", "accept", "reject")]
    improved = [scored(valid_case, "failure-001", "accept", "accept")]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    monkeypatch.setattr(
        runner, "load_api_key", lambda *_: pytest.fail("compare loaded API key")
    )
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    assert (
        runner.main(
            [
                "compare",
                "--baseline",
                str(tmp_path / "baseline"),
                "--improved",
                str(tmp_path / "improved"),
                "--analysis",
                str(analysis_path),
                "--output",
                str(tmp_path / "reports"),
            ]
        )
        == 0
    )
    assert (tmp_path / "reports/comparison-summary.json").is_file()


def test_model_fingerprint_change_marks_causal_claim_as_contaminated(
    tmp_path, monkeypatch, valid_case
):
    baseline = [scored(valid_case, "failure-001", "accept", "reject")]
    improved = [scored(valid_case, "failure-001", "accept", "accept")]
    left_raw = raw_run(baseline, fingerprint="fp-before")
    right_raw = raw_run(improved, fingerprint="fp-after")
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    summary = reporting.generate_reports(
        tmp_path / "baseline",
        tmp_path / "improved",
        analysis_path,
        tmp_path / "reports",
    )
    resume = (tmp_path / "reports/resume-bullet.md").read_text(encoding="utf-8")
    assert summary.causal_contamination
    assert "因果归因受污染" in resume
    assert "完成定向改进" not in resume


def test_unknown_attempt_case_is_rejected(tmp_path, monkeypatch, valid_case):
    baseline = [scored(valid_case, "known-001", "accept", "reject")]
    improved = [item.model_copy() for item in baseline]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    left_raw["attempts.jsonl"] += (
        runner.canonical(attempt("unknown-001").model_dump(mode="json")) + b"\n"
    )
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(
        analysis_path,
        select_improvement(
            baseline,
            impact_weights=CONFIG["impact_weights"],
            baseline_raw_inputs=left_raw,
        ).model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="unknown or duplicate"):
        reporting.build_comparison(
            tmp_path / "baseline", tmp_path / "improved", analysis_path
        )


def test_corrupt_review_tail_is_rejected_without_modifying_bytes(tmp_path):
    from test_runner import completed_run

    completed_run(tmp_path)
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_bytes(b'{"case_id":')
    before = reviews.read_bytes()
    with pytest.raises(ValueError, match="newline|malformed|corrupt"):
        reporting._load_run(tmp_path)
    assert reviews.read_bytes() == before


def test_dataset_replacement_after_snapshot_is_rejected_without_report_write(
    tmp_path, monkeypatch
):
    from test_runner import completed_run

    run_path = tmp_path / "baseline"
    completed_run(run_path)
    original_snapshot = reporting.snapshot_run_inputs

    def replace_after_snapshot(path: Path):
        raw = original_snapshot(path)
        dataset = path / "dataset.jsonl"
        dataset.write_bytes(raw["dataset.jsonl"] + b"\n")
        return raw

    monkeypatch.setattr(reporting, "snapshot_run_inputs", replace_after_snapshot)
    with pytest.raises(ValueError, match="changed after snapshot"):
        reporting._load_run(run_path)
    assert not (run_path / "summary.json").exists()


def test_analysis_replacement_after_snapshot_is_rejected_without_report_write(
    tmp_path, monkeypatch, valid_case
):
    baseline = [scored(valid_case, "failure-001", "accept", "reject")]
    improved = [scored(valid_case, "failure-001", "accept", "accept")]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))

    def replace_after_snapshot(*args, **kwargs):
        parsed = json.loads(analysis_path.read_bytes())
        analysis_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        assert kwargs["analysis"].model_dump() == analysis.model_dump()

    monkeypatch.setattr(reporting, "verify_improvement", replace_after_snapshot)
    output = tmp_path / "reports"
    with pytest.raises(ValueError, match="changed after snapshot"):
        reporting.generate_reports(
            tmp_path / "baseline",
            tmp_path / "improved",
            analysis_path,
            output,
        )
    assert not output.exists()
    assert not (tmp_path / "baseline/summary.json").exists()
    assert not (tmp_path / "improved/summary.json").exists()


@pytest.mark.parametrize(
    ("review_case_ids", "expected_status", "expected_count"),
    [
        ([], "not_measured", None),
        (["review-001"], "partial", 1),
        (["review-001", "review-002"], "measured", 2),
    ],
)
def test_review_measurement_completeness_states(
    tmp_path,
    monkeypatch,
    valid_case,
    review_case_ids,
    expected_status,
    expected_count,
):
    baseline = [
        scored(valid_case, "review-001", "manual_review", "manual_review"),
        scored(valid_case, "review-002", "manual_review", "manual_review"),
    ]
    improved = [item.model_copy() for item in baseline]
    left_raw = raw_run(baseline, review_case_ids=review_case_ids)
    right_raw = raw_run(improved, review_case_ids=review_case_ids)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    summary = reporting.build_comparison(
        tmp_path / "baseline", tmp_path / "improved", analysis_path
    )
    assert summary.baseline.review_measurement_status == expected_status
    assert summary.baseline.reviewed_case_count == expected_count
    if expected_status == "not_measured":
        assert summary.baseline.total_human_modified_field_count is None
        assert summary.baseline.total_review_duration_ms is None
    else:
        assert summary.baseline.total_human_modified_field_count == expected_count
        assert summary.baseline.total_review_duration_ms == expected_count * 1250


def test_multiple_identifiers_within_run_contaminate_comparison(
    tmp_path, monkeypatch, valid_case
):
    baseline = [
        scored(valid_case, "failure-001", "accept", "reject"),
        scored(valid_case, "success-001", "accept", "accept"),
    ]
    improved = [
        scored(valid_case, "failure-001", "accept", "accept"),
        scored(valid_case, "success-001", "accept", "accept"),
    ]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    left_attempts = [
        json.loads(line) for line in left_raw["attempts.jsonl"].splitlines()
    ]
    left_attempts[-1]["returned_model"] = "different-model"
    left_attempts[-1]["system_fingerprint"] = "different-fingerprint"
    left_raw["attempts.jsonl"] = b"".join(
        runner.canonical(item) + b"\n" for item in left_attempts
    )
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    summary = reporting.generate_reports(
        tmp_path / "baseline",
        tmp_path / "improved",
        analysis_path,
        tmp_path / "reports",
    )
    assert summary.causal_status == "contaminated"
    assert any(
        "multiple returned models" in item
        for item in summary.causal_contamination_reasons
    )
    resume = (tmp_path / "reports/resume-bullet.md").read_text(encoding="utf-8")
    assert "完成定向改进" not in resume


def test_missing_identifiers_make_causal_claim_unverifiable(
    tmp_path, monkeypatch, valid_case
):
    baseline = [scored(valid_case, "failure-001", "accept", "reject")]
    improved = [scored(valid_case, "failure-001", "accept", "accept")]
    left_raw = raw_run(baseline, fingerprint=None, returned_model=None)
    right_raw = raw_run(improved, fingerprint=None, returned_model=None)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    summary = reporting.generate_reports(
        tmp_path / "baseline",
        tmp_path / "improved",
        analysis_path,
        tmp_path / "reports",
    )
    assert summary.causal_status == "unverifiable"
    resume = (tmp_path / "reports/resume-bullet.md").read_text(encoding="utf-8")
    assert "完成定向改进" not in resume
    assert "不可验证" in resume


@pytest.mark.parametrize(
    ("field", "baseline_identity", "improved_identity", "expected_reason"),
    [
        (
            "returned_model",
            "model-before",
            "model-after",
            "returned model identities differs between runs",
        ),
        (
            "system_fingerprint",
            "fingerprint-before",
            "fingerprint-after",
            "system fingerprint identities differs between runs",
        ),
    ],
)
def test_observed_identity_drift_overrides_partial_missing_identifiers(
    tmp_path,
    monkeypatch,
    valid_case,
    field,
    baseline_identity,
    improved_identity,
    expected_reason,
):
    baseline = [scored_after_repair(valid_case, "case-001")]
    improved = [scored_after_repair(valid_case, "case-001")]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    for raw, observed in (
        (left_raw, baseline_identity),
        (right_raw, improved_identity),
    ):
        attempts = [json.loads(line) for line in raw["attempts.jsonl"].splitlines()]
        attempts[0][field] = observed
        attempts[1][field] = None
        raw["attempts.jsonl"] = b"".join(
            runner.canonical(item) + b"\n" for item in attempts
        )
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))

    summary = reporting.build_comparison(
        tmp_path / "baseline", tmp_path / "improved", analysis_path
    )

    assert summary.causal_status == "contaminated"
    assert expected_reason in summary.causal_contamination_reasons
    assert any("missing" in item for item in summary.causal_contamination_reasons)


def test_same_identifier_with_different_attempt_count_remains_clean(
    tmp_path, monkeypatch, valid_case
):
    baseline = [scored(valid_case, "case-001", "accept", "reject")]
    improved = [scored_after_repair(valid_case, "case-001")]
    left_raw, right_raw = raw_run(baseline), raw_run(improved)
    install_runs(monkeypatch, baseline, improved, left_raw, right_raw)
    analysis = select_improvement(
        baseline,
        impact_weights=CONFIG["impact_weights"],
        baseline_raw_inputs=left_raw,
    )
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    summary = reporting.build_comparison(
        tmp_path / "baseline", tmp_path / "improved", analysis_path
    )
    assert summary.baseline.returned_models == summary.improved.returned_models
    assert summary.baseline.http_attempt_count == 1
    assert summary.improved.http_attempt_count == 2
    assert summary.causal_status == "clean"
    assert summary.causal_contamination_reasons == []
