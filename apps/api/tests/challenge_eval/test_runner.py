import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_validation import response_for

from tracerisk.challenge_eval import runner
from tracerisk.challenge_eval.client import CompletionAttempt
from tracerisk.challenge_eval.dataset import load_cases

ROOT = Path(__file__).resolve().parents[4]
CONFIG = json.loads(
    (ROOT / "datasets/evaluation/model_challenge_config.json").read_text()
)


class FakeClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.messages = []

    async def complete_once(self, messages):
        self.messages.append(messages)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def completion(content='{"findings": []}', **updates):
    return CompletionAttempt(
        latency_ms=1,
        status="succeeded",
        retryable=False,
        requested_model="deepseek-flash",
        request_parameters={},
        response_content=content,
        **updates,
    )


def execute(path, case, client, **kwargs):
    return asyncio.run(runner.run_cases(path, [case], CONFIG, client=client, **kwargs))


def test_repair_budget_and_empty_findings(tmp_path, valid_case):
    client = FakeClient(completion("bad"), completion())
    results = execute(tmp_path, valid_case, client)
    assert len(client.messages) == 2
    assert client.messages[1][-1]["role"] == "user"
    assert "structure_error" in client.messages[1][-1]["content"]
    assert results[0].validation_repair_count == 1
    assert results[0].actual_action == "reject"
    assert len(runner.read_journal(tmp_path / "attempts.jsonl")) == 2
    empty = FakeClient(completion())
    assert execute(tmp_path / "empty", valid_case, empty)[0].first_attempt_usable
    assert len(empty.messages) == 1


def test_successful_attempt_replayed_after_crash(tmp_path, valid_case, monkeypatch):
    real = runner.append_journal

    def crash(path, payload):
        real(path, payload)
        if path.name == "attempts.jsonl":
            raise RuntimeError("crash")

    monkeypatch.setattr(runner, "append_journal", crash)
    with pytest.raises(RuntimeError):
        execute(tmp_path, valid_case, FakeClient(completion()))
    monkeypatch.setattr(runner, "append_journal", real)
    client = FakeClient()
    assert execute(tmp_path, valid_case, client)[0].actual_action == "reject"
    assert not client.messages
    assert execute(tmp_path, valid_case, client)[0].actual_action == "reject"


def test_crash_after_event_has_null_timing(tmp_path, valid_case, monkeypatch):
    real = runner.append_journal

    def crash(path, payload):
        real(path, payload)
        if path.name == "case-events.jsonl":
            raise RuntimeError("crash")

    monkeypatch.setattr(runner, "append_journal", crash)
    with pytest.raises(RuntimeError):
        execute(tmp_path, valid_case, FakeClient(completion()))
    monkeypatch.setattr(runner, "append_journal", real)
    result = execute(tmp_path, valid_case, FakeClient())[0]
    assert result.failure_localization_ms is None
    assert result.failure_localization_status == "interrupted_before_finalize"


def test_durable_event_is_inside_timing(tmp_path, valid_case, monkeypatch):
    state = {"clock": 0.0}
    real = runner.append_journal

    def append(path, payload):
        real(path, payload)
        if path.name == "case-events.jsonl":
            assert "failure_localization_ms" not in payload
            state["clock"] = 0.25

    monkeypatch.setattr(runner, "append_journal", append)
    result = execute(
        tmp_path, valid_case, FakeClient(completion()), monotonic=lambda: state["clock"]
    )[0]
    assert result.failure_localization_ms == 250


def test_journal_recovers_only_unterminated_tail(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":')
    assert runner.read_journal(path) == [{"a": 1}]
    runner.append_journal(path, {"b": 2})
    assert runner.read_journal(path) == [{"a": 1}, {"b": 2}]
    path.write_bytes(b'broken\n{"a":1}\n')
    with pytest.raises(ValueError, match="corrupt"):
        runner.read_journal(path)
    path.write_bytes(b'{"a":1}\n[]')
    with pytest.raises(ValueError, match="corrupt"):
        runner.read_journal(path)
    path.write_bytes(b'{"a":1}\nbroken\n')
    with pytest.raises(ValueError, match="corrupt"):
        runner.read_journal(path)


def test_append_fsync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner.os, "fsync", lambda fd: calls.append(fd))
    runner.append_journal(tmp_path / "j", {"x": "中"})
    assert len(calls) == 1
    assert (tmp_path / "j").read_bytes() == b'{"x":"\xe4\xb8\xad"}\n'


def test_key_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError):
        runner.load_api_key(tmp_path)
    (tmp_path / ".env").write_text('MODEL_API_KEY="secret-sentinel"\n')
    assert runner.load_api_key(tmp_path).get_secret_value() == "secret-sentinel"
    monkeypatch.setenv("MODEL_API_KEY", "ambient")
    with pytest.raises(ValueError, match="ambient"):
        runner.load_api_key(tmp_path)


@pytest.mark.parametrize(
    "hour,expected",
    [(0, "off_peak"), (1, "peak"), (4, "off_peak"), (6, "peak"), (10, "off_peak")],
)
def test_peak_interval_boundaries(hour, expected):
    assert (
        runner.pricing_tier(datetime(2026, 10, 23, hour, tzinfo=UTC), CONFIG)
        == expected
    )


def test_manifest_rejects_mismatch_and_completed(tmp_path):
    manifest = runner.build_manifest(ROOT, phase="baseline", run_id="test")
    runner.prepare_run(tmp_path, manifest)
    runner.prepare_run(tmp_path, manifest)
    changed = manifest.model_copy(update={"fingerprint": "0" * 64})
    with pytest.raises(ValueError, match="fingerprint"):
        runner.prepare_run(tmp_path, changed)
    manifest.status = "completed"
    runner.write_json(tmp_path / "manifest.json", manifest.model_dump(mode="json"))
    with pytest.raises(ValueError, match="completed"):
        runner.prepare_run(tmp_path, manifest)


def test_manifest_includes_cli_and_production_graph():
    manifest = runner.build_manifest(ROOT, phase="baseline", run_id="test")
    assert "scripts/run_model_challenge.py" in manifest.source_inventory
    assert "apps/api/src/tracerisk/ai/claims.py" in manifest.source_inventory
    assert "apps/api/src/tracerisk/ai/validation.py" in manifest.source_inventory
    assert manifest.environment["dependencies"]


def completed_run(path, *, first_review=False, review_finding_count=1):
    manifest = runner.build_manifest(ROOT, phase="baseline", run_id="baseline")
    runner.prepare_run(path, manifest)
    shutil.copyfile(ROOT / runner.DATASET, path / "dataset.jsonl")
    shutil.copyfile(ROOT / runner.CONFIG, path / "config.json")
    cases = load_cases(path / "dataset.jsonl").cases
    responses = [completion() for case in cases]
    if first_review:
        draft = json.loads(response_for(cases[0].chunks[0], review_recommended=True))
        draft["findings"] = [
            {**draft["findings"][0], "finding_key": f"finding-{index + 1}"}
            for index in range(review_finding_count)
        ]
        responses[0] = completion(json.dumps(draft))
    asyncio.run(runner.run_cases(path, cases, CONFIG, client=FakeClient(*responses)))
    manifest.status = "completed"
    runner.write_json(path / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def test_empty_findings_in_conflict_remains_usable_reject(tmp_path):
    case = next(
        c
        for c in load_cases(ROOT / runner.DATASET).cases
        if c.category == "source_conflict"
    )
    client = FakeClient(completion())
    result = execute(tmp_path, case, client)[0]
    assert result.actual_action == "reject"
    assert result.first_attempt_usable
    assert len(client.messages) == 1


def test_retry_after_crash_uses_original_and_stamps_request(
    tmp_path, valid_case, monkeypatch
):
    failed = CompletionAttempt(
        latency_ms=1,
        status="failed",
        retryable=True,
        requested_model="deepseek-flash",
        request_parameters={},
        error_category="timeout",
    )
    append = runner.append_journal

    def crash(path, value):
        append(path, value)
        raise RuntimeError("crash")

    monkeypatch.setattr(runner, "append_journal", crash)
    with pytest.raises(RuntimeError):
        execute(tmp_path, valid_case, FakeClient(failed))
    monkeypatch.setattr(runner, "append_journal", append)
    client = FakeClient(completion("bad"))
    stamp = datetime(2026, 10, 23, 6, tzinfo=UTC)
    result = execute(tmp_path, valid_case, client, utcnow=lambda: stamp)[0]
    assert result.http_attempt_count == 2
    assert result.transport_retry_count == 1
    assert client.messages == [runner.build_messages(valid_case)]
    assert result.attempts[-1].requested_at == stamp
    assert result.attempts[-1].pricing_tier == "peak"
    assert execute(tmp_path, valid_case, FakeClient())[0] == result


@pytest.mark.parametrize(
    "category", ["malformed_response", "null_content", "non_stop_finish"]
)
def test_client_output_errors_consume_second_attempt_as_repair(
    tmp_path, valid_case, category
):
    failed = CompletionAttempt(
        latency_ms=1,
        status="failed",
        retryable=False,
        requested_model="deepseek-flash",
        request_parameters={},
        error_category=category,
    )
    client = FakeClient(failed, failed)
    result = execute(tmp_path, valid_case, client)[0]
    assert result.validation_repair_count == 1
    assert result.http_attempt_count == 2
    assert "structure_error" in client.messages[-1][-1]["content"]


def test_analysis_recomputes_scores_and_rejects_tampering(tmp_path):
    completed_run(tmp_path)
    analysis = runner.analyze_run(tmp_path)
    assert set(analysis.baseline_input_hashes) == {
        "manifest.json",
        "dataset.jsonl",
        "config.json",
        "attempts.jsonl",
        "case-events.jsonl",
        "cases.jsonl",
    }
    cases = runner.read_journal(tmp_path / "cases.jsonl")
    cases[0]["first_attempt_usable"] = False
    (tmp_path / "cases.jsonl").write_bytes(
        b"".join(runner.canonical(c) + b"\n" for c in cases)
    )
    with pytest.raises(ValueError, match="recomputed"):
        runner.analyze_run(tmp_path)


def test_changed_routing_does_not_rewrite_baseline_analysis(tmp_path, monkeypatch):
    completed_run(tmp_path)
    before = runner.analyze_run(tmp_path)
    assert before.selected_problem.changed_dimension == "review_routing"
    monkeypatch.setattr(
        runner,
        "validate_response",
        lambda *args: pytest.fail("baseline must use its durable outcomes"),
    )
    assert runner.analyze_run(tmp_path) == before


def test_review_validates_without_persisting_submitted_secret(tmp_path):
    completed_run(tmp_path, first_review=True)
    before = {
        name: (tmp_path / name).read_bytes()
        for name in ("attempts.jsonl", "cases.jsonl")
    }
    state = {"clock": 0.0}
    secret = "SECRET_SENTINEL_REVIEW_4281"
    pending = iter(
        [
            {"decision": "accept", "changes": {"finding-1": {"risk_type": secret}}},
            {"decision": "accept", "changes": {"finding-1": {"claim_code": secret}}},
            {
                "decision": "accept",
                "changes": {"finding-1": {"title": secret}},
                "rationale": secret,
            },
        ]
    )
    displays = []

    def display(value):
        displays.append(value)
        if '"draft"' in value:
            state["clock"] = 10.0

    def reply(prompt):
        state["clock"] += 1.0
        return json.dumps(next(pending))

    results = runner.review_cases(
        tmp_path, input_fn=reply, display=display, monotonic=lambda: state["clock"]
    )
    reviews = runner.read_journal(tmp_path / "reviews.jsonl")
    assert len(reviews) == 1
    assert reviews[0]["changed_fields"] == ["title"]
    assert reviews[0]["review_duration_seconds"] == 3
    assert set(reviews[0]) == {
        "case_id",
        "decision",
        "changed_fields",
        "changed_field_count",
        "review_duration_seconds",
        "validation_status",
    }
    assert secret not in (tmp_path / "reviews.jsonl").read_text()
    assert secret not in json.dumps([r.model_dump(mode="json") for r in results])
    assert secret not in json.dumps(displays)
    assert all(
        (tmp_path / name).read_bytes() == value for name, value in before.items()
    )
    reviewed = next(r for r in results if r.case_id == reviews[0]["case_id"])
    assert reviewed.human_modified_field_count == 1
    assert reviewed.review_measurement_status == "measured"
    runner.review_cases(
        tmp_path, input_fn=lambda _: pytest.fail("must skip reviewed case")
    )


def test_review_counts_changed_instances_across_findings(tmp_path):
    completed_run(tmp_path, first_review=True, review_finding_count=2)
    submission = json.dumps(
        {
            "decision": "manual_review",
            "changes": {
                "finding-1": {"title": "Revised title one"},
                "finding-2": {"title": "Revised title two"},
            },
        }
    )
    results = runner.review_cases(
        tmp_path, input_fn=lambda _: submission, display=lambda _: None
    )
    review = runner.read_journal(tmp_path / "reviews.jsonl")[0]
    assert review["changed_fields"] == ["title"]
    assert review["changed_field_count"] == 2
    result = next(item for item in results if item.case_id == review["case_id"])
    assert result.human_modified_field_count == 2


@pytest.mark.parametrize(
    "fields,count", [([], 1), (["title"], 0), (["title", "summary"], 1)]
)
def test_review_field_metadata_rejects_inconsistent_counts(tmp_path, fields, count):
    completed_run(tmp_path, first_review=True)
    result = next(
        item
        for item in runner.recompute_run(tmp_path)[1]
        if item.actual_action == "manual_review"
    )
    review = runner.ReviewRecord(
        case_id=result.case_id,
        decision="accept",
        changed_fields=fields,
        changed_field_count=count,
        review_duration_seconds=1,
        validation_status="valid",
    )
    with pytest.raises(ValueError, match="field metadata"):
        runner.validate_review_record(review, result)


def test_selected_function_decorator_add_change_remove_are_frozen(tmp_path):
    baseline = tmp_path / "baseline"
    original = completed_run(baseline)
    source = tmp_path / "source"
    for name in {
        *original.source_inventory,
        runner.CONFIG,
        runner.DATASET,
        runner.LOCK,
    }:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    filename, _ = runner.SELECTABLE["review_routing"]
    target = source / filename
    original_source = target.read_bytes()
    analysis_path = tmp_path / "analysis.json"
    for before, after in (
        (b"", b"@staticmethod\n"),
        (b"@staticmethod\n", b"@classmethod\n"),
        (b"@staticmethod\n", b""),
    ):
        target.write_bytes(
            original_source.replace(b"def route_review(", before + b"def route_review(")
        )
        baseline_manifest = runner.build_manifest(
            source, phase="baseline", run_id="baseline"
        )
        baseline_manifest.status = "completed"
        runner.write_json(
            baseline / "manifest.json", baseline_manifest.model_dump(mode="json")
        )
        runner.write_json(
            analysis_path, runner.analyze_run(baseline).model_dump(mode="json")
        )
        target.write_bytes(
            original_source.replace(b"def route_review(", after + b"def route_review(")
        )
        improved = runner.build_manifest(
            source,
            phase="improved",
            run_id="improved",
            changed_dimension="review_routing",
        )
        with pytest.raises(ValueError, match="top-level function"):
            runner.verify_improvement(baseline, analysis_path, improved)


def test_single_function_guard_and_recomputed_analysis(tmp_path):
    baseline = tmp_path / "baseline"
    original = completed_run(baseline)
    analysis = runner.analyze_run(baseline)
    analysis_path = tmp_path / "analysis.json"
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    source = tmp_path / "source"
    for name in {
        *original.source_inventory,
        runner.CONFIG,
        runner.DATASET,
        runner.LOCK,
    }:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    filename, function = runner.SELECTABLE[analysis.selected_problem.changed_dimension]
    target = source / filename
    target.write_bytes(
        target.read_bytes().replace(b"return conflict or any(", b"return True or any(")
    )
    improved = runner.build_manifest(
        source, phase="improved", run_id="improved", changed_dimension="review_routing"
    )
    runner.verify_improvement(baseline, analysis_path, improved)
    cli = source / runner.CLI
    cli.write_text(cli.read_text() + "\n# shared wrapper change\n")
    changed = runner.build_manifest(
        source, phase="improved", run_id="improved", changed_dimension="review_routing"
    )
    with pytest.raises(ValueError, match="source file"):
        runner.verify_improvement(baseline, analysis_path, changed)
    shutil.copyfile(ROOT / runner.CLI, cli)
    patched = target.read_bytes()
    for extra in (
        b"\nSHARED_CONSTANT = 1\n",
        b"\ndef helper():\n    return 1\n",
        b"\nimport math\n",
    ):
        target.write_bytes(patched + extra)
        changed = runner.build_manifest(
            source,
            phase="improved",
            run_id="improved",
            changed_dimension="review_routing",
        )
        with pytest.raises(ValueError, match="top-level function"):
            runner.verify_improvement(baseline, analysis_path, changed)
    target.write_bytes(patched)
    analysis.impact_weights["wrong_reject"] = 99
    runner.write_json(analysis_path, analysis.model_dump(mode="json"))
    with pytest.raises(ValueError, match="recomputed baseline"):
        runner.verify_improvement(baseline, analysis_path, improved)


@pytest.mark.parametrize("command", ["analyze", "review", "compare"])
def test_offline_commands_do_not_load_key(tmp_path, monkeypatch, command):
    monkeypatch.setattr(
        runner, "load_api_key", lambda *_: pytest.fail("offline loaded key")
    )
    args = [command, "--output", str(tmp_path / "output.json")]
    args += (
        ["--baseline", str(tmp_path), "--improved", str(tmp_path)]
        if command == "compare"
        else ["--run", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as exc:
        runner.main(args)
    assert exc.value.code == 2


def test_help_never_loads_key(monkeypatch, capsys):
    monkeypatch.setattr(
        runner, "load_api_key", lambda *_: pytest.fail("help loaded key")
    )
    with pytest.raises(SystemExit) as exc:
        runner.main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert all(
        command in help_text
        for command in ("check", "baseline", "analyze", "improved", "compare", "review")
    )


@pytest.mark.parametrize("malformed", [False, True])
def test_check_separate_and_excluded(tmp_path, monkeypatch, capsys, malformed):
    from pydantic import SecretStr

    monkeypatch.setattr(runner, "load_api_key", lambda *_: SecretStr("test-only"))
    monkeypatch.setattr(
        runner, "load_cases", lambda *_: pytest.fail("check must not load cases")
    )
    response = completion().model_copy(
        update={
            "status": "failed" if malformed else "succeeded",
            "error_category": "malformed_response" if malformed else None,
            "response_content": "PRIVATE_RESPONSE_SENTINEL",
            "returned_model": "returned-model",
            "system_fingerprint": "fingerprint-1",
            "input_tokens": 2,
            "output_tokens": 1,
            "total_tokens": 3,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 2,
        }
    )
    client = FakeClient(response)
    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kwargs: client)
    output = tmp_path / "connectivity-check.json"
    runner.main(
        ["check", "--config", str(ROOT / runner.CONFIG), "--output", str(output)],
        root=ROOT,
    )
    assert len(client.messages) == 1
    assert list(tmp_path.iterdir()) == [output]
    stored = json.loads(output.read_text())
    shown = json.loads(capsys.readouterr().out)
    assert stored == shown
    assert stored["status"] == response.status
    assert stored["system_fingerprint"] == "fingerprint-1"
    assert stored["returned_model"] == "returned-model"
    assert stored["usage"]["input_tokens"] == 2
    assert stored["latency_ms"] == 1
    assert stored["conservative_estimated_cost_usd"] is not None
    assert "PRIVATE_RESPONSE_SENTINEL" not in output.read_text()
    assert "test-only" not in output.read_text()


@pytest.mark.parametrize("command", ["baseline", "improved"])
def test_documented_run_commands_accept_explicit_inputs(tmp_path, monkeypatch, command):
    from pydantic import SecretStr

    calls = []
    monkeypatch.setattr(runner, "load_api_key", lambda *_: SecretStr("test-only"))
    monkeypatch.setattr(runner, "verify_improvement", lambda *args: calls.append(args))
    monkeypatch.setattr(
        runner,
        "DeepSeekClient",
        lambda **kwargs: FakeClient(*(completion() for _ in range(40))),
    )
    analysis = tmp_path / "analysis.json"
    runner.write_json(
        analysis,
        {
            "baseline_input_hashes": {},
            "impact_weights": {},
            "impact_weights_hash": "0" * 64,
            "attributions": {},
            "candidates": [],
            "selected_problem": None,
        },
    )
    dataset = tmp_path / "selected-data.jsonl"
    config = tmp_path / "selected-config.json"
    shutil.copyfile(ROOT / runner.DATASET, dataset)
    shutil.copyfile(ROOT / runner.CONFIG, config)
    # Different raw bytes prove the selected input paths reach the manifest.
    dataset.write_bytes(dataset.read_bytes() + b"\n")
    config.write_bytes(config.read_bytes() + b"\n")
    args = [
        command,
        "--dataset",
        str(dataset),
        "--config",
        str(config),
        "--output",
        str(tmp_path / "run"),
    ]
    if command == "improved":
        args += ["--baseline", str(tmp_path / "baseline"), "--analysis", str(analysis)]
    assert runner.main(args, root=ROOT) == 0
    manifest = runner.read_manifest(tmp_path / "run/manifest.json")
    assert manifest.dataset_hash == runner.digest(dataset.read_bytes())
    assert manifest.config_hash == runner.digest(config.read_bytes())
    assert len(runner.read_journal(tmp_path / "run/attempts.jsonl")) == 40
    assert bool(calls) == (command == "improved")


def test_documented_offline_commands(tmp_path, monkeypatch):
    from tracerisk.challenge_eval import reporting

    baseline = tmp_path / "baseline"
    completed_run(baseline)
    monkeypatch.setattr(
        runner, "load_api_key", lambda *_: pytest.fail("offline loaded key")
    )
    analysis = tmp_path / "analysis.json"
    assert (
        runner.main(["analyze", "--baseline", str(baseline), "--output", str(analysis)])
        == 0
    )
    assert runner.main(["review", "--run", str(baseline)]) == 0
    improved = tmp_path / "improved"
    shutil.copytree(baseline, improved)
    manifest = runner.read_manifest(improved / "manifest.json")
    manifest.phase = "improved"
    manifest.fingerprint = runner.manifest_fingerprint(manifest)
    runner.write_json(improved / "manifest.json", manifest.model_dump(mode="json"))
    # This Task 6 fixture copies baseline bytes rather than applying a selected
    # function patch. Task 7 verifies the real single-dimension guard elsewhere.
    monkeypatch.setattr(reporting, "verify_improvement", lambda *args: None)
    assert (
        runner.main(
            [
                "compare",
                "--baseline",
                str(baseline),
                "--improved",
                str(improved),
                "--analysis",
                str(analysis),
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    result = json.loads(
        (tmp_path / "comparison-summary.json").read_text(encoding="utf-8")
    )
    assert result["baseline"]["case_count"] == 40
    assert result["improved"]["case_count"] == 40
    assert result["provenance"]["analysis"]["analysis.json"] == runner.digest(
        analysis.read_bytes()
    )


def test_permanent_failure_has_no_retry(tmp_path, valid_case):
    failed = CompletionAttempt(
        latency_ms=1,
        status="failed",
        retryable=False,
        requested_model="deepseek-flash",
        request_parameters={},
        error_category="authentication",
        http_status=401,
    )
    client = FakeClient(failed)
    result = execute(tmp_path, valid_case, client)[0]
    assert result.http_attempt_count == 1
    assert result.execution_status == "failed"


def test_interrupted_final_case_append_recovers_from_event(
    tmp_path, valid_case, monkeypatch
):
    append = runner.append_journal

    def interrupt(path, value):
        if path.name == "cases.jsonl":
            path.write_bytes(b'{"case_id":')
            raise RuntimeError("interrupted final append")
        append(path, value)

    monkeypatch.setattr(runner, "append_journal", interrupt)
    with pytest.raises(RuntimeError):
        execute(tmp_path, valid_case, FakeClient(completion()))
    monkeypatch.setattr(runner, "append_journal", append)
    result = execute(tmp_path, valid_case, FakeClient())[0]
    assert result.failure_localization_ms is None
    assert result.failure_localization_status == "interrupted_before_finalize"


def test_import_graph_recurses_through_added_local_module(tmp_path):
    source = tmp_path / "repo"
    manifest = runner.build_manifest(ROOT, phase="baseline", run_id="test")
    for name in manifest.source_inventory:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    module = source / "apps/api/src/tracerisk/ai/helper_probe.py"
    nested = source / "apps/api/src/tracerisk/ai/nested_probe.py"
    module.write_text("from . import nested_probe\n")
    nested.write_text("VALUE = 1\n")
    entry = source / runner.SURFACE / "validation.py"
    entry.write_bytes(entry.read_bytes() + b"\nimport tracerisk.ai.helper_probe\n")
    files, _ = runner.source_inventory(source)
    assert module.relative_to(source).as_posix() in files
    assert nested.relative_to(source).as_posix() in files
