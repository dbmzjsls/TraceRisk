"""Failure publication and ownership checks without touching PostgreSQL."""

import json
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from scripts import database_lifecycle as lifecycle
from scripts import run_evaluation as cli
from scripts.database_lifecycle import OwnedDatabase

from tracerisk.evaluation.runner import evaluate_rule_cases


@pytest.fixture
def isolated_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    root = tmp_path
    results = root / "docs/evaluation/results.json"
    markdown = root / "docs/evaluation/results.md"
    marker = root / "docs/evaluation/results.running.json"
    results.parent.mkdir(parents=True)
    baseline = {"database": "tracerisk", "alembic_revision": "0011", "tables": {}}
    baseline_path = root / ".superpowers/sdd/stage-6-retained-baseline.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    for name, value in {
        "ROOT": root,
        "RESULTS": results,
        "MARKDOWN": markdown,
        "MARKER": marker,
        "RUNS": root / "docs/evaluation/runs",
    }.items():
        monkeypatch.setattr(cli, name, value)
    monkeypatch.setattr(cli, "GROUPS", ("test_rules.py",))
    monkeypatch.setattr(cli, "retained_snapshot", lambda _: baseline)
    monkeypatch.setattr(cli, "current_database", lambda _, database: database)
    monkeypatch.setattr(cli, "_source_hashes", lambda: {"source": "a" * 64})
    monkeypatch.setattr(cli.uuid4, "__call__", lambda: UUID(int=1), raising=False)
    monkeypatch.setattr(cli, "uuid4", lambda: UUID(int=1))
    monkeypatch.setattr(cli.command, "upgrade", lambda *_: None)

    async def initialized(_: object) -> None:
        return None

    monkeypatch.setattr(cli, "_initialize", initialized)
    dropped: list[str] = []

    def create(_: object, *, purpose: str, run_id: str) -> OwnedDatabase:
        assert purpose == "eval"
        return OwnedDatabase(f"tracerisk_eval_{run_id}", "eval", run_id)

    def drop(_: object, owned: OwnedDatabase) -> None:
        dropped.append(owned.name)

    monkeypatch.setattr(cli, "create_owned_database", create)
    monkeypatch.setattr(cli, "drop_owned_database", drop)
    for key in (
        "POSTGRES_DB",
        "MODEL_ADAPTER",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
    ):
        monkeypatch.setenv(key, os.environ.get(key, "test-original"))
    return dropped


def _passed(group: str, _: dict[str, str], run_id: str) -> dict[str, object]:
    return {
        "group": group,
        "run_id": run_id,
        "exit_code": 0,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
    }


def test_owned_database_rejects_retained_and_foreign_targets() -> None:
    run_id = UUID(int=1).hex
    for name in (
        "tracerisk",
        "tracerisk_demo",
        "tracerisk_eval_foreign",
        "tracerisk_e2e_" + run_id,
    ):
        with pytest.raises(ValueError):
            OwnedDatabase(name, "eval", run_id)


def test_publish_detects_corrupt_markdown_and_partial_replace(
    isolated_cli: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_cli
    document = {"run_id": "a" * 32, "status": "success", "mock": {}, "errors": []}
    cli.publish(document)
    assert cli.artifacts_match(run_id=document["run_id"])
    cli.MARKDOWN.write_text("stale markdown", encoding="utf-8")
    assert not cli.artifacts_match(run_id=document["run_id"])
    original = cli.os.replace

    def fail_markdown(source: Path, target: Path) -> None:
        if target == cli.MARKDOWN:
            raise OSError("simulated artifact interruption")
        original(source, target)

    monkeypatch.setattr(cli.os, "replace", fail_markdown)
    with pytest.raises(OSError, match="simulated artifact interruption"):
        cli.publish(document)
    assert not cli.artifacts_match(run_id=document["run_id"])
    assert not list(cli.RESULTS.parent.glob("*.tmp"))


def test_failed_pytest_replaces_previous_success_and_cleans_up(
    isolated_cli: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli.publish({"run_id": "f" * 32, "status": "success", "mock": {}, "errors": []})

    def failed(group: str, _: dict[str, str], run_id: str) -> dict[str, object]:
        return {**_passed(group, {}, run_id), "exit_code": 1, "passed": 0, "failed": 1}

    monkeypatch.setattr(cli, "_test_group", failed)
    assert cli.run() == 1
    result = json.loads(cli.RESULTS.read_text(encoding="utf-8"))
    assert result["run_id"] == UUID(int=1).hex
    assert result["status"] == "failed"
    assert result["mock"]["fresh_tests"]["test_rules.py"]["failed"] == 1
    assert isolated_cli == [f"tracerisk_eval_{UUID(int=1).hex}"]
    assert cli.artifacts_match(run_id=result["run_id"])
    assert not cli.MARKER.exists()


def test_failed_metric_group_and_failed_final_write_publish_current_failure(
    isolated_cli: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_test_group", _passed)

    async def failed_metric(_: object, __: object, ___: object) -> dict[str, object]:
        raise ValueError("deliberate metric failure")

    monkeypatch.setattr(cli, "_metrics", failed_metric)
    original = cli.publish
    calls = 0

    def interrupted(document: dict[str, object]) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated final write failure")
        return original(document)

    monkeypatch.setattr(cli, "publish", interrupted)
    assert cli.run() == 1
    result = json.loads(cli.RESULTS.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert any("metric runner" in error for error in result["errors"])
    assert any("Artifact publication failed" in error for error in result["errors"])
    assert isolated_cli == [f"tracerisk_eval_{UUID(int=1).hex}"]
    assert cli.artifacts_match(run_id=result["run_id"])


def test_cleanup_failure_marks_run_failed(
    isolated_cli: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_cli
    monkeypatch.setattr(cli, "_test_group", _passed)

    async def failed_metric(_: object, __: object, ___: object) -> dict[str, object]:
        raise ValueError("metric failed")

    def failed_drop(_: object, __: OwnedDatabase) -> None:
        raise OSError("drop failure")

    monkeypatch.setattr(cli, "_metrics", failed_metric)
    monkeypatch.setattr(cli, "drop_owned_database", failed_drop)
    assert cli.run() == 1
    result = json.loads(cli.RESULTS.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["cleanup"]["status"] == "failed"


def test_post_create_identity_failure_keeps_ownership_for_cleanup(
    isolated_cli: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[bool] = []

    class Admin:
        def __enter__(self) -> "Admin":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, _: object, params: object = None) -> "Admin":
            if params is None:
                created.append(True)
            return self

        def fetchone(self) -> None:
            return None

    monkeypatch.setattr(lifecycle, "_admin", lambda _: Admin())
    monkeypatch.setattr(cli, "create_owned_database", lifecycle.create_owned_database)

    def fail_identity(_: object, __: str) -> str:
        raise OSError("post-create connection failed")

    monkeypatch.setattr(cli, "current_database", fail_identity)
    assert cli.run() == 1
    result = json.loads(cli.RESULTS.read_text(encoding="utf-8"))
    owned_name = f"tracerisk_eval_{UUID(int=1).hex}"
    assert created == [True]
    assert isolated_cli == [owned_name]
    assert result["cleanup"] == {"status": "dropped", "database": owned_name}
    assert "OSError in database identity verification" in result["errors"]


def test_settings_failure_replaces_previous_success(
    isolated_cli: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.publish({"run_id": "f" * 32, "status": "success", "mock": {}, "errors": []})

    def invalid_settings(**_: object) -> None:
        raise ValueError("sensitive configuration value")

    monkeypatch.setattr(cli, "Settings", invalid_settings)
    assert cli.run() == 1
    result = json.loads(cli.RESULTS.read_text(encoding="utf-8"))
    assert result["run_id"] == UUID(int=1).hex
    assert result["status"] == "failed"
    assert result["cleanup"] == {"status": "not_created"}
    assert result["errors"] == ["ValueError in settings validation"]
    assert isolated_cli == []
    assert cli.artifacts_match(run_id=result["run_id"])
    assert not cli.MARKER.exists()
    assert "sensitive configuration value" not in cli.RESULTS.read_text(
        encoding="utf-8"
    )


def test_late_metric_failure_retains_completed_group(
    isolated_cli: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_test_group", _passed)

    async def fail_after_rules(
        _: object,
        on_start: Callable[[str], None],
        on_complete: Callable[[str, object], None],
    ) -> dict[str, object]:
        on_start("rules")
        on_complete("rules", evaluate_rule_cases("a" * 64))
        on_start("retrieval")
        raise ValueError("retrieval failed")

    monkeypatch.setattr(cli, "_metrics", fail_after_rules)
    assert cli.run() == 1
    result = json.loads(cli.RESULTS.read_text(encoding="utf-8"))
    metric = result["mock"]["metrics"]["rule_exact_match_accuracy"]
    assert (metric["numerator"], metric["denominator"]) == (13, 13)
    assert metric["formula"] == "exact matching cases / labeled cases"
    assert len(metric["sample_ids"]) == 13
    assert metric["source_snapshot_sha256"] == "a" * 64
    assert result["mock"]["metric_groups"] == {
        "rules": "passed",
        "retrieval": "failed",
    }
    assert result["cleanup"]["status"] == "dropped"
    assert isolated_cli == [f"tracerisk_eval_{UUID(int=1).hex}"]


def test_source_hashes_name_approved_stage_6_plan_separately() -> None:
    sources = cli._source_hashes()
    plan = cli.ROOT / "docs/superpowers/plans/2026-09-28-stage-6.md"
    roadmap = cli.ROOT / "TraceRisk-v3-实施计划.md"
    assert sources["stage6_plan_sha256"] == sha256(plan.read_bytes()).hexdigest()
    assert sources["roadmap_sha256"] == sha256(roadmap.read_bytes()).hexdigest()
    assert sources["stage6_plan_sha256"] != sources["roadmap_sha256"]
