"""Single guarded Stage 6 Mock/Fake evaluation gate."""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/api/src"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from tracerisk.config import Settings  # noqa: E402
from tracerisk.evaluation.metrics import MetricResult, canonical_json  # noqa: E402
from tracerisk.evaluation.runner import evaluate_metrics  # noqa: E402
from tracerisk.retrieval.embeddings import FakeEmbedding  # noqa: E402
from tracerisk.retrieval.evaluation import source_snapshot_hash  # noqa: E402
from tracerisk.retrieval.indexing import IndexContract, build_index  # noqa: E402
from tracerisk.synthetic.generator import generate_catalog  # noqa: E402
from tracerisk.synthetic.importer import import_catalog  # noqa: E402
from tracerisk.workflows.queue import create_queue  # noqa: E402
from tracerisk.workflows.spike_graph import initialize_checkpoints  # noqa: E402

from scripts.database_lifecycle import (  # noqa: E402
    OwnedDatabase,
    create_owned_database,
    current_database,
    drop_owned_database,
    retained_snapshot,
)

RESULTS = ROOT / "docs/evaluation/results.json"
MARKDOWN = ROOT / "docs/evaluation/results.md"
MARKER = ROOT / "docs/evaluation/results.running.json"
RUNS = ROOT / "docs/evaluation/runs"
DATASET = ROOT / "datasets/evaluation/retrieval_cases.jsonl"
GROUPS = (
    "test_rules.py",
    "test_retrieval_recall.py",
    "test_citations.py",
    "test_workflow_recovery.py",
)


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        "approved_spec_sha256": _hash_file(
            ROOT / "docs/superpowers/specs/2026-09-28-stage-6-design.md"
        ),
        "stage6_plan_sha256": _hash_file(
            ROOT / "docs/superpowers/plans/2026-09-28-stage-6.md"
        ),
        "roadmap_sha256": _hash_file(ROOT / "TraceRisk-v3-实施计划.md"),
        "synthetic_catalog_snapshot_sha256": source_snapshot_hash(),
        "retrieval_jsonl_sha256": _hash_file(DATASET),
    }


def _render_markdown(document: dict[str, Any], json_hash: str) -> str:
    run_id = document["run_id"]
    lines = [
        "# Stage 6 automatic evaluation",
        "",
        f"Run ID: `{run_id}`",
        f"Status: `{document['status']}`",
        f"JSON SHA-256: `{json_hash}`",
        "",
        "Mode: Mock model, Fake embedding. Real model: not run.",
        "",
    ]
    mock = document.get("mock", {})
    tests = mock.get("fresh_tests", {})
    if tests:
        lines += [
            "## Required tests",
            "",
            "| Group | Exit | Passed | Failed | Errors |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, result in tests.items():
            lines.append(
                f"| {name} | {result['exit_code']} | {result['passed']} | "
                f"{result['failed']} | {result['errors']} |"
            )
        lines.append("")
    metrics = mock.get("metrics", {})
    if metrics:
        lines += [
            "## Metrics",
            "",
            "| Metric | Numerator | Denominator | Value | Status |",
            "|---|---:|---:|---:|---|",
        ]
        for name, metric in metrics.items():
            value = metric.get("value")
            shown = "N/A" if value is None else f"{value:.6f}"
            lines.append(
                f"| {name} | {metric['numerator']} | {metric['denominator']} | "
                f"{shown} | {metric['status']} |"
            )
        lines += [
            "",
            "Recall@5 is the existing per-case macro mean. Fake results are a "
            "deterministic pipeline baseline, not semantic-model quality.",
            "",
        ]
    if document.get("errors"):
        lines += ["## Failures", ""]
        lines.extend(f"- {error}" for error in document["errors"])
        lines.append("")
    if document.get("cleanup"):
        lines += [f"Database cleanup: `{document['cleanup']['status']}`.", ""]
    return "\n".join(lines)


def publish(document: dict[str, Any]) -> str:
    """Replace both latest artifacts; marker remains until consistency is verified."""
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(document)
    digest = sha256(payload).hexdigest()
    markdown = _render_markdown(document, digest).encode("utf-8")
    token = document["run_id"]
    json_temp = RESULTS.with_name(f"results.{token}.json.tmp")
    md_temp = MARKDOWN.with_name(f"results.{token}.md.tmp")
    try:
        json_temp.write_bytes(payload)
        md_temp.write_bytes(markdown)
        os.replace(json_temp, RESULTS)
        os.replace(md_temp, MARKDOWN)
        if not artifacts_match(run_id=token):
            raise RuntimeError("JSON and Markdown hashes do not agree")
    finally:
        json_temp.unlink(missing_ok=True)
        md_temp.unlink(missing_ok=True)
    return digest


def artifacts_match(*, run_id: str) -> bool:
    if not RESULTS.exists() or not MARKDOWN.exists():
        return False
    payload = RESULTS.read_bytes()
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        return False
    markdown = MARKDOWN.read_text(encoding="utf-8")
    return result.get("run_id") == run_id and (
        f"Run ID: `{run_id}`" in markdown
        and f"JSON SHA-256: `{sha256(payload).hexdigest()}`" in markdown
    )


def _count_pytest(output: str) -> dict[str, int]:
    summary = output.rsplit("\n", 3)[-3:]
    summary_text = " ".join(summary)
    return {
        label: sum(
            int(n)
            for n in re.findall(
                rf"\b(\d+) {label if label != 'errors' else 'errors?'}\b",
                summary_text,
            )
        )
        for label in ("passed", "failed", "errors", "skipped")
    }


def _test_group(group: str, env: dict[str, str], run_id: str) -> dict[str, object]:
    RUNS.mkdir(parents=True, exist_ok=True)
    command_line = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(RUNS / f"{run_id}-{group}-tmp"),
        "--tb=line",
        str(ROOT / "apps/api/tests/evaluation" / group),
    ]
    completed = subprocess.run(
        command_line,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    counts = _count_pytest(completed.stdout + completed.stderr)
    evidence = {
        "run_id": run_id,
        "group": group,
        "exit_code": completed.returncode,
        **counts,
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{run_id}-{group}.json").write_bytes(canonical_json(evidence))
    return evidence


async def _initialize(settings: Settings) -> None:
    if current_database(settings, settings.postgres_db) != settings.postgres_db:
        raise ValueError("Evaluation connection targets another database")
    await initialize_checkpoints()
    async with create_queue(settings).open_async() as app:
        await app.schema_manager.apply_schema_async()
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            await import_catalog(session, generate_catalog())
            await build_index(session, FakeEmbedding(), IndexContract.fake())
    finally:
        await engine.dispose()


async def _metrics(
    settings: Settings,
    on_group_start: Callable[[str], None],
    on_group_complete: Callable[[str, object], None],
) -> dict[str, object]:
    engine = create_async_engine(settings.database_url)
    try:
        return await evaluate_metrics(
            settings,
            async_sessionmaker(engine, expire_on_commit=False),
            DATASET,
            on_group_start=on_group_start,
            on_group_complete=on_group_complete,
        )
    finally:
        await engine.dispose()


def _metric_document(results: dict[str, object]) -> dict[str, object]:
    rules = results["rules"]
    retrieval = results["retrieval"]
    assert isinstance(rules, dict) and isinstance(retrieval, dict)
    domains = cast(dict[str, MetricResult], rules["per_domain"])
    return {
        "rule_exact_match_accuracy": asdict(cast(MetricResult, rules["overall"])),
        **{f"rule_{domain}": asdict(metric) for domain, metric in domains.items()},
        "macro_mean_recall_at_5": asdict(cast(MetricResult, retrieval["metric"])),
        "citation_legality": asdict(cast(MetricResult, results["citation_legality"])),
        "evidence_coverage": asdict(cast(MetricResult, results["evidence_coverage"])),
        "structured_output_success": asdict(
            cast(MetricResult, results["structured_output"])
        ),
    }


def _record_metric_group(document: dict[str, Any], name: str, result: object) -> None:
    metrics = document["mock"]["metrics"]
    if name == "rules":
        rule_result = cast(dict[str, Any], result)
        metrics["rule_exact_match_accuracy"] = asdict(rule_result["overall"])
        for domain, metric in rule_result["per_domain"].items():
            metrics[f"rule_{domain}"] = asdict(metric)
        document["mock"]["rule_cases"] = rule_result["cases"]
    elif name == "retrieval":
        retrieval_result = cast(dict[str, Any], result)
        metrics["macro_mean_recall_at_5"] = asdict(retrieval_result["metric"])
        document["mock"]["retrieval_cases"] = retrieval_result["existing_evaluation"][
            "cases"
        ]
    elif name in {"citations", "coverage", "structured_output"}:
        key = {
            "citations": "citation_legality",
            "coverage": "evidence_coverage",
            "structured_output": "structured_output_success",
        }[name]
        metrics[key] = asdict(cast(MetricResult, result))
    else:
        raise ValueError("Unknown metric group")
    document["mock"]["metric_groups"][name] = "passed"


def run(*, keep_database: bool = False) -> int:
    run_id = uuid4().hex
    started = datetime.now(UTC).isoformat()
    previous_database = os.environ.get("POSTGRES_DB")
    for key, value in {
        "MODEL_ADAPTER": "mock",
        "EMBEDDING_PROVIDER": "fake",
        "EMBEDDING_MODEL": "fake-sha256-v1",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }.items():
        os.environ[key] = value
    retained_settings: Settings | None = None
    baseline: dict[str, object] | None = None
    document: dict[str, Any] = {
        "schema_version": "stage6-evaluation-v1",
        "run_id": run_id,
        "started_at_utc": started,
        "status": "running",
        "environment": {
            "model_adapter": "mock",
            "embedding_provider": "fake",
            "embedding_model": "fake-sha256-v1",
            "tracing": "disabled",
        },
        "source_hashes": {},
        "mock": {"mode": "mock", "fresh_tests": {}, "metrics": {}, "metric_groups": {}},
        "real": {"status": "not_run", "reason": "No real-model call was made."},
        "errors": [],
        "cleanup": {"status": "pending"},
    }
    owned: OwnedDatabase | None = None
    active_metric_group: str | None = None
    phase = "running marker"
    try:
        MARKER.write_bytes(canonical_json({"run_id": run_id, "status": "running"}))
        publish(document)
        phase = "settings validation"
        retained_settings = Settings(postgres_db="tracerisk")
        phase = "baseline verification"
        baseline = retained_snapshot(retained_settings)
        baseline_path = ROOT / ".superpowers/sdd/stage-6-retained-baseline.json"
        if baseline != json.loads(baseline_path.read_text(encoding="utf-8")):
            raise RuntimeError("Retained database differs from frozen Stage 6 baseline")
        document["source_hashes"] = _source_hashes()
        phase = "database creation"
        owned = create_owned_database(retained_settings, purpose="eval", run_id=run_id)
        phase = "database identity verification"
        if current_database(retained_settings, owned.name) != owned.name:
            raise RuntimeError("Connected database differs from created target")
        phase = "database initialization"
        os.environ["POSTGRES_DB"] = owned.name
        settings = Settings()
        assert settings.postgres_db == owned.name
        config = Config(str(ROOT / "apps/api/alembic.ini"))
        command.upgrade(config, "head")
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            runner.run(_initialize(settings))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "apps/api/src")
        for group in GROUPS:
            phase = f"required tests: {group}"
            evidence = _test_group(group, env, run_id)
            document["mock"]["fresh_tests"][group] = evidence
            if evidence["exit_code"] != 0:
                document["errors"].append(
                    f"Required test group failed: {group} "
                    f"(exit {evidence['exit_code']})"
                )
            if retained_snapshot(retained_settings) != baseline:
                raise RuntimeError(f"Retained database changed after {group}")
            publish(document)
        if document["errors"]:
            raise RuntimeError("Required tests failed")
        phase = "metric runner"

        def on_group_start(name: str) -> None:
            nonlocal active_metric_group, phase
            active_metric_group = name
            phase = f"metric group {name}"
            document["mock"]["metric_groups"][name] = "running"
            publish(document)

        def on_group_complete(name: str, result: object) -> None:
            nonlocal active_metric_group, phase
            _record_metric_group(document, name, result)
            publish(document)
            active_metric_group = None
            phase = "metric runner"

        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            metric_results = runner.run(
                _metrics(settings, on_group_start, on_group_complete)
            )
        if document["mock"]["metrics"] != _metric_document(metric_results):
            raise ValueError("Incremental metrics differ from final result")
        document["mock"]["negative_response_tests"] = metric_results[
            "negative_response_tests"
        ]
        if retained_snapshot(retained_settings) != baseline:
            raise RuntimeError("Retained database changed after metric runner")
        RUNS.mkdir(parents=True, exist_ok=True)
        (RUNS / f"{run_id}-metrics.json").write_bytes(
            canonical_json(
                {
                    "run_id": run_id,
                    "metrics": document["mock"]["metrics"],
                    "metric_groups": document["mock"]["metric_groups"],
                }
            )
        )
        document["status"] = "success"
    except BaseException as exc:
        document["status"] = "failed"
        if active_metric_group is not None:
            document["mock"]["metric_groups"][active_metric_group] = "failed"
        elif phase == "metric runner":
            document["mock"]["metric_groups"]["runner"] = "failed"
        message = f"{type(exc).__name__} in {phase}"
        if message not in document["errors"]:
            document["errors"].append(message)
    finally:
        if owned is not None and retained_settings is not None:
            if keep_database:
                document["cleanup"] = {
                    "status": "retained_for_debugging",
                    "database": owned.name,
                }
            else:
                try:
                    drop_owned_database(retained_settings, owned)
                    document["cleanup"] = {"status": "dropped", "database": owned.name}
                except Exception as exc:
                    document["status"] = "failed"
                    document["cleanup"] = {"status": "failed", "database": owned.name}
                    document["errors"].append(
                        f"Database cleanup failed: {type(exc).__name__}"
                    )
        else:
            document["cleanup"] = {"status": "not_created"}
        if baseline is not None and retained_settings is not None:
            try:
                if retained_snapshot(retained_settings) != baseline:
                    document["status"] = "failed"
                    document["errors"].append(
                        "Retained database differs from frozen baseline"
                    )
            except Exception as exc:
                document["status"] = "failed"
                document["errors"].append(
                    f"Retained comparison failed: {type(exc).__name__}"
                )
        document["finished_at_utc"] = datetime.now(UTC).isoformat()
        try:
            publish(document)
            MARKER.unlink(missing_ok=True)
        except Exception as exc:
            document["status"] = "failed"
            document["errors"].append(
                f"Artifact publication failed: {type(exc).__name__}"
            )
            # The marker makes any previously published success non-current.
            try:
                publish(document)
                MARKER.unlink(missing_ok=True)
            except Exception:
                pass
        if previous_database is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = previous_database
    return 0 if document["status"] == "success" and not MARKER.exists() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-database",
        action="store_true",
        help="Retain the generated target for debugging",
    )
    args = parser.parse_args()
    raise SystemExit(run(keep_database=args.keep_database))
