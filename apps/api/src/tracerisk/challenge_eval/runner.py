"""Durable, resumable orchestration for the frozen model challenge."""

import argparse
import ast
import asyncio
import importlib.metadata
import json
import os
import platform
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol

from dotenv import dotenv_values
from pydantic import SecretStr

from tracerisk.ai.schemas import StructuredRiskFindings
from tracerisk.challenge_eval.attribution import (
    ImprovementAnalysis,
    select_improvement,
    verify_analysis,
)
from tracerisk.challenge_eval.client import (
    CompletionAttempt,
    DeepSeekClient,
    DeepSeekRequestSettings,
)
from tracerisk.challenge_eval.dataset import (
    load_cases,
    parse_cases,
    validate_frozen_shape,
)
from tracerisk.challenge_eval.prompts import build_messages
from tracerisk.challenge_eval.schemas import (
    AttemptRecord,
    ChallengeCase,
    ReviewRecord,
    RunManifest,
)
from tracerisk.challenge_eval.scoring import ScoredCase, score_case
from tracerisk.challenge_eval.scoring import _cost as estimate_cost
from tracerisk.challenge_eval.validation import (
    ValidationOutcome,
    build_validation_feedback,
    validate_response,
)

DATASET = "datasets/evaluation/model_challenge_cases.jsonl"
CONFIG = "datasets/evaluation/model_challenge_config.json"
LOCK = "apps/api/uv.lock"
CLI = "scripts/run_model_challenge.py"
SURFACE = "apps/api/src/tracerisk/challenge_eval"
SELECTABLE = {
    "prompt": (f"{SURFACE}/prompts.py", "build_messages"),
    "context_order": (f"{SURFACE}/prompts.py", "order_context_chunks"),
    "validation_feedback": (f"{SURFACE}/validation.py", "build_validation_feedback"),
    "review_routing": (f"{SURFACE}/validation.py", "route_review"),
}
REVIEW_FIELDS = {
    "claim_code",
    "title",
    "summary",
    "reasoning",
    "evidence_citations",
    "evidence_sufficiency",
    "confidence_level",
    "review_recommended",
}


class CompletionClient(Protocol):
    async def complete_once(
        self, messages: Sequence[dict[str, str]]
    ) -> CompletionAttempt: ...


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def write_json(path: Path, value: object) -> None:
    write_bytes(path, canonical(value) + b"\n")


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_journal(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_journal(path: Path) -> list[dict[str, Any]]:
    """Recover only an invalid, unterminated final append; reject all other damage."""
    if not path.exists():
        return []
    raw = path.read_bytes()
    records: list[dict[str, Any]] = []
    offset = 0
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                with path.open("r+b") as stream:
                    stream.truncate(offset)
                    stream.flush()
                    os.fsync(stream.fileno())
                return records
            raise ValueError(f"corrupt journal: {path.name}, line {index + 1}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"corrupt journal: {path.name}, line {index + 1}")
        records.append(record)
        offset += len(line)
    # A complete JSON object whose newline was interrupted is retained safely.
    if raw and not raw.endswith(b"\n"):
        with path.open("ab") as stream:
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    return records


def parse_journal_bytes(raw: bytes, name: str) -> list[dict[str, Any]]:
    """Strictly parse a complete journal snapshot without touching its file."""
    if raw and not raw.endswith(b"\n"):
        raise ValueError(f"corrupt journal: {name} must end with newline")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(raw.splitlines(), 1):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"corrupt journal: {name}, line {index}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"corrupt journal: {name}, line {index}")
        records.append(record)
    return records


def load_api_key(root: Path) -> SecretStr:
    if "MODEL_API_KEY" in os.environ:
        raise ValueError("ambient MODEL_API_KEY is forbidden; use repository-root .env")
    path = root / ".env"
    if not path.is_file():
        raise ValueError("repository-root .env must contain MODEL_API_KEY")
    value = dotenv_values(path, interpolate=False).get("MODEL_API_KEY")
    if not value or not value.strip():
        raise ValueError("repository-root .env must contain MODEL_API_KEY")
    return SecretStr(value)


def pricing_tier(
    requested_at: datetime, config: Mapping[str, Any]
) -> Literal["peak", "off_peak"]:
    policy = config["pricing_policy"]
    stamp = requested_at.astimezone(UTC)
    clock = stamp.strftime("%H:%M")
    inside = stamp.strftime("%A") in policy["peak_weekdays"] and any(
        start <= clock < end for start, end in policy["peak_intervals"]
    )
    # The frozen config has no checked-in calendar. Unknown holidays are peak.
    return "peak" if inside else "off_peak"


def source_inventory(root: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Walk every static local import, including package initializers and CLI."""
    source_root = root / "apps/api/src"
    pending = [root / CLI, *(root / SURFACE).glob("*.py")]
    files: dict[str, str] = {}
    behaviors: dict[str, dict[str, str]] = {}
    while pending:
        path = pending.pop()
        relative = path.relative_to(root).as_posix()
        if relative in files:
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        tree = ast.parse(text)
        files[relative] = digest(raw)
        nodes = {
            f"{index}:{getattr(node, 'name', type(node).__name__)}": digest(
                ast.dump(node, include_attributes=False).encode()
            )
            for index, node in enumerate(tree.body)
        }
        # Freeze comments, imports and all nonselectable source bytes too.
        lines = text.splitlines(keepends=True)
        for node in reversed(tree.body):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (relative, node.name) in SELECTABLE.values()
            ):
                # Decorators execute outside the selected function body and stay frozen.
                lines[node.lineno - 1 : node.end_lineno] = [f"<selected:{node.name}>\n"]
        nodes["__frozen_source__"] = digest("".join(lines).encode())
        behaviors[relative] = nodes
        package = (
            relative.removeprefix("apps/api/src/").removesuffix(".py").split("/")[:-1]
        )
        for imported in ast.walk(tree):
            modules = []
            if isinstance(imported, ast.Import):
                modules = [alias.name for alias in imported.names]
            elif isinstance(imported, ast.ImportFrom):
                prefix = (
                    ".".join(package[: len(package) - imported.level + 1])
                    if imported.level
                    else ""
                )
                module = ".".join(part for part in (prefix, imported.module) if part)
                modules = [module, *(f"{module}.{a.name}" for a in imported.names)]
            for module in modules:
                if module != "tracerisk" and not module.startswith("tracerisk."):
                    continue
                parts = module.split(".")
                for length in range(1, len(parts) + 1):
                    base = source_root.joinpath(*parts[:length])
                    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
                        if candidate.is_file():
                            pending.append(candidate)
    return dict(sorted(files.items())), dict(sorted(behaviors.items()))


def build_manifest(
    root: Path,
    *,
    phase: Literal["baseline", "improved"],
    run_id: str,
    changed_dimension: Any = None,
    dataset_path: Path | None = None,
    config_path: Path | None = None,
) -> RunManifest:
    dataset_path = dataset_path or root / DATASET
    config_path = config_path or root / CONFIG
    config = json.loads(config_path.read_bytes())
    files, behaviors = source_inventory(root)
    environment = {
        "python": sys.version,
        "os": platform.platform(),
        "dependencies": dict(
            sorted(
                (d.metadata["Name"], d.version)
                for d in importlib.metadata.distributions()
                if d.metadata["Name"]
            )
        ),
        "config": config,
        "cli_hash": files[CLI],
        "check_excluded": True,
    }
    fields = dict(
        phase=phase,
        dataset_hash=digest(dataset_path.read_bytes()),
        config_hash=digest(config_path.read_bytes()),
        lock_hash=digest((root / LOCK).read_bytes()),
        prompt_hash=files[f"{SURFACE}/prompts.py"],
        requested_model=config["model"],
        environment=environment,
        source_inventory=files,
        behavior_inventory=behaviors,
        changed_dimension=changed_dimension,
    )
    return RunManifest(
        run_id=run_id,
        created_at=datetime.now(UTC),
        status="incomplete",
        fingerprint=digest(canonical(fields)),
        **fields,
    )


def manifest_fingerprint(manifest: RunManifest) -> str:
    return digest(
        canonical(
            manifest.model_dump(
                mode="json",
                exclude={
                    "run_id",
                    "created_at",
                    "status",
                    "fingerprint",
                },
            )
        )
    )


def parse_manifest(raw: bytes) -> RunManifest:
    manifest = RunManifest.model_validate_json(raw)
    if manifest.fingerprint != manifest_fingerprint(manifest):
        raise ValueError("manifest fingerprint does not match its content")
    return manifest


def read_manifest(path: Path) -> RunManifest:
    return parse_manifest(path.read_bytes())


def prepare_run(output: Path, manifest: RunManifest) -> RunManifest:
    path = output / "manifest.json"
    if path.exists():
        saved = read_manifest(path)
        if saved.fingerprint != manifest.fingerprint:
            raise ValueError("run fingerprint mismatch")
        if saved.status == "completed":
            raise ValueError("completed run cannot be overwritten")
        return saved
    if output.exists() and any(output.iterdir()):
        raise ValueError("nonempty output has no resumable manifest")
    write_json(path, manifest.model_dump(mode="json"))
    return manifest


def outcomes(
    case: ChallengeCase, attempts: Sequence[AttemptRecord]
) -> list[ValidationOutcome | None]:
    return [
        validate_response(case, a.response_content or "")
        if a.status == "succeeded"
        else None
        for a in attempts
    ]


def next_request(
    attempts: Sequence[AttemptRecord], validations: Sequence[ValidationOutcome | None]
) -> tuple[Any, str | None] | None:
    if not attempts:
        return "original", None
    if len(attempts) >= 2:
        return None
    last = attempts[-1]
    outcome = validations[-1]
    if last.status == "failed" and last.retryable:
        return "transport_retry", None
    if outcome is not None and outcome.error_type:
        return "validation_repair", outcome.feedback
    if last.status == "failed" and last.error_category in {
        "malformed_response",
        "null_content",
        "non_stop_finish",
    }:
        return "validation_repair", build_validation_feedback(
            "structure_error", "Invalid or incomplete response"
        )
    return None


async def run_cases(
    output: Path,
    cases: Sequence[ChallengeCase],
    config: Mapping[str, Any],
    *,
    client: CompletionClient,
    monotonic: Callable[[], float] = time.monotonic,
    utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[ScoredCase]:
    by_id = {case.case_id: case for case in cases}
    attempts_by_id: dict[str, list[AttemptRecord]] = {key: [] for key in by_id}
    for raw in read_journal(output / "attempts.jsonl"):
        record = AttemptRecord.model_validate(raw)
        if record.case_id not in by_id:
            raise ValueError("attempt references unknown case")
        records = attempts_by_id[record.case_id]
        if record.attempt_number != len(records) + 1:
            raise ValueError("duplicate or out-of-order attempt")
        records.append(record)
    events = unique_cases(read_journal(output / "case-events.jsonl"), by_id)
    completed = unique_cases(read_journal(output / "cases.jsonl"), by_id)
    results = []
    for case in cases:
        attempts = attempts_by_id[case.case_id]
        if case.case_id in completed:
            result = ScoredCase.model_validate(completed[case.case_id])
            if result.attempts != attempts or case.case_id not in events:
                raise ValueError("case result disagrees with durable journals")
            results.append(result)
            continue
        if case.case_id in events:
            event = {
                key: value
                for key, value in events[case.case_id].items()
                if key != "validations"
            }
            result = ScoredCase.model_validate(
                {
                    **event,
                    "failure_localization_ms": None,
                    "failure_localization_status": "interrupted_before_finalize",
                }
            )
            if result.attempts != attempts:
                raise ValueError("case event disagrees with attempts")
        else:
            while True:
                started = monotonic()
                validations = outcomes(case, attempts)
                request = next_request(attempts, validations)
                if request is None:
                    break
                kind, feedback = request
                messages = build_messages(case)
                if feedback:
                    messages.append({"role": "user", "content": feedback})
                requested_at = utcnow().astimezone(UTC)
                response = await client.complete_once(messages)
                attempt = AttemptRecord(
                    case_id=case.case_id,
                    attempt_number=len(attempts) + 1,
                    request_kind=kind,
                    requested_at=requested_at,
                    pricing_tier=pricing_tier(requested_at, config),
                    **response.model_dump(),
                )
                append_journal(
                    output / "attempts.jsonl", attempt.model_dump(mode="json")
                )
                attempts.append(attempt)
            result = score_case(
                case, attempts, validations, config, failure_localization_ms=None
            )
            event = result.model_dump(
                mode="json",
                exclude={"failure_localization_ms", "failure_localization_status"},
            )
            event["validations"] = [
                outcome.model_dump(mode="json") if outcome else None
                for outcome in validations
            ]
            append_journal(output / "case-events.jsonl", event)
            elapsed = max(0, int((monotonic() - started) * 1000))
            result = result.model_copy(
                update={
                    "failure_localization_ms": elapsed,
                    "failure_localization_status": "measured",
                }
            )
        append_journal(output / "cases.jsonl", result.model_dump(mode="json"))
        results.append(result)
    return results


def unique_cases(
    records: Sequence[dict[str, Any]], cases: Mapping[str, object]
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("case_id")
        if not isinstance(key, str) or key not in cases or key in indexed:
            raise ValueError("unknown or duplicate journal case")
        indexed[key] = record
    return indexed


def snapshot_run_inputs(path: Path) -> dict[str, bytes]:
    names = (
        "manifest.json",
        "dataset.jsonl",
        "config.json",
        "attempts.jsonl",
        "case-events.jsonl",
        "cases.jsonl",
    )
    raw = {name: (path / name).read_bytes() for name in names}
    if (path / "reviews.jsonl").exists():
        raw["reviews.jsonl"] = (path / "reviews.jsonl").read_bytes()
    return raw


def recompute_run(
    path: Path,
    *,
    raw_inputs: Mapping[str, bytes] | None = None,
    strict_read_only: bool = False,
) -> tuple[RunManifest, list[ScoredCase], dict[str, bytes]]:
    raw = dict(raw_inputs) if raw_inputs is not None else snapshot_run_inputs(path)
    manifest = parse_manifest(raw["manifest.json"])
    if manifest.status != "completed":
        raise ValueError("analysis requires completed run")
    if manifest.environment.get("run_kind") == "check":
        raise ValueError("check artifacts are excluded from evaluation metrics")
    if (
        digest(raw["dataset.jsonl"]) != manifest.dataset_hash
        or digest(raw["config.json"]) != manifest.config_hash
    ):
        raise ValueError("snapshot hash mismatch")
    dataset = parse_cases(raw["dataset.jsonl"])
    validate_frozen_shape(dataset)
    config = json.loads(raw["config.json"])
    if manifest.environment["config"] != config:
        raise ValueError("manifest configuration differs from snapshot")
    cases = {case.case_id: case for case in dataset.cases}

    def journal(name: str) -> list[dict[str, Any]]:
        if strict_read_only:
            return parse_journal_bytes(raw.get(name, b""), name)
        return read_journal(path / name)

    final = unique_cases(journal("cases.jsonl"), cases)
    events = unique_cases(journal("case-events.jsonl"), cases)
    reviews = unique_cases(journal("reviews.jsonl"), cases)
    grouped: dict[str, list[AttemptRecord]] = {key: [] for key in cases}
    for item in journal("attempts.jsonl"):
        attempt = AttemptRecord.model_validate(item)
        if attempt.case_id not in cases:
            raise ValueError("unknown attempt case")
        grouped[attempt.case_id].append(attempt)
    if final.keys() != cases.keys() or events.keys() != cases.keys():
        raise ValueError("completed run has missing cases")
    scores = []
    for key, case in cases.items():
        saved = ScoredCase.model_validate(final[key])
        # Persisted outcomes retain the actual baseline routing boundary. Calling
        # today's route_review here would rewrite baseline evidence after a patch.
        validations = [
            ValidationOutcome.model_validate(item) if item is not None else None
            for item in events[key].get("validations", [])
        ]
        result = score_case(
            case,
            grouped[key],
            validations,
            config,
            failure_localization_ms=saved.failure_localization_ms,
        )
        if saved.model_dump() != result.model_dump():
            raise ValueError("saved case differs from recomputed attempts")
        event_payload = {
            name: value for name, value in events[key].items() if name != "validations"
        }
        if event_payload != result.model_dump(
            mode="json",
            exclude={"failure_localization_ms", "failure_localization_status"},
        ):
            raise ValueError("case event differs from recomputed attempts")
        if key in reviews:
            review = ReviewRecord.model_validate(reviews[key])
            validate_review_record(review, result)
            result = score_case(
                case,
                grouped[key],
                validations,
                config,
                failure_localization_ms=saved.failure_localization_ms,
                human_modified_field_count=review.changed_field_count,
                review_duration_ms=round(review.review_duration_seconds * 1000),
            )
        scores.append(result)
    # Recovery-mode callers hash the retained bytes after final-line repair.
    if not strict_read_only:
        raw = snapshot_run_inputs(path)
    return manifest, scores, raw


def analyze_run(path: Path) -> ImprovementAnalysis:
    manifest, scores, raw = recompute_run(path)
    return select_improvement(
        scores,
        impact_weights=json.loads(raw["config.json"])["impact_weights"],
        baseline_raw_inputs=raw,
    )


def verify_improvement(
    baseline_path: Path,
    analysis_path: Path,
    improved: RunManifest,
    *,
    baseline_raw_inputs: Mapping[str, bytes] | None = None,
    strict_read_only: bool = False,
    analysis: ImprovementAnalysis | None = None,
) -> None:
    baseline, scores, raw = recompute_run(
        baseline_path,
        raw_inputs=baseline_raw_inputs,
        strict_read_only=strict_read_only,
    )
    if strict_read_only and analysis is None:
        raise ValueError("strict verification requires snapshotted analysis")
    saved = (
        analysis
        if analysis is not None
        else ImprovementAnalysis.model_validate_json(analysis_path.read_bytes())
    )
    verify_analysis(
        saved,
        scores,
        impact_weights=json.loads(raw["config.json"])["impact_weights"],
        baseline_raw_inputs=raw,
    )
    if baseline.phase != "baseline" or saved.selected_problem is None:
        raise ValueError("baseline has no selected improvement")
    dimension = saved.selected_problem.changed_dimension
    if improved.changed_dimension != dimension:
        raise ValueError("improvement dimension differs from analysis")
    for field in (
        "dataset_hash",
        "config_hash",
        "lock_hash",
        "requested_model",
        "environment",
    ):
        left, right = getattr(baseline, field), getattr(improved, field)
        if field == "environment":
            left, right = (
                {k: v for k, v in value.items() if k != "cli_hash"}
                for value in (left, right)
            )
        if left != right:
            raise ValueError(f"frozen {field} changed")
    allowed_path, allowed_function = SELECTABLE[dimension]
    if baseline.source_inventory.keys() != improved.source_inventory.keys():
        raise ValueError("source inventory changed")
    changed_files = {
        key
        for key in baseline.source_inventory
        if baseline.source_inventory[key] != improved.source_inventory[key]
    }
    if changed_files != {allowed_path}:
        raise ValueError("only selected function source file may change")
    changes = []
    for path in baseline.behavior_inventory.keys() | improved.behavior_inventory.keys():
        before = baseline.behavior_inventory.get(path, {})
        after = improved.behavior_inventory.get(path, {})
        for key in before.keys() | after.keys():
            if before.get(key) != after.get(key):
                changes.append((path, key))
    if (
        len(changes) != 1
        or changes[0][0] != allowed_path
        or changes[0][1].split(":", 1)[-1] != allowed_function
    ):
        raise ValueError("exactly the selected top-level function AST node must change")


def validate_review_record(review: ReviewRecord, result: ScoredCase) -> None:
    if result.actual_action != "manual_review" or review.validation_status != "valid":
        raise ValueError("review does not describe a valid manual-review decision")
    if (
        not set(review.changed_fields) <= REVIEW_FIELDS
        or len(review.changed_fields) != len(set(review.changed_fields))
        or review.changed_field_count < len(review.changed_fields)
        or bool(review.changed_field_count) != bool(review.changed_fields)
    ):
        raise ValueError("invalid review field metadata")


def review_cases(
    path: Path,
    *,
    input_fn: Callable[[str], str] = input,
    display: Callable[[str], None] = print,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[ScoredCase]:
    _, results, _ = recompute_run(path)
    dataset = {case.case_id: case for case in load_cases(path / "dataset.jsonl").cases}
    existing = unique_cases(read_journal(path / "reviews.jsonl"), dataset)
    for result in results:
        if result.actual_action != "manual_review" or result.case_id in existing:
            continue
        case = dataset[result.case_id]
        original = StructuredRiskFindings.model_validate_json(
            result.attempts[-1].response_content or ""
        )
        draft = original.model_dump(mode="json")
        display(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "chunks": [c.model_dump(mode="json") for c in case.chunks],
                    "draft": draft,
                },
                ensure_ascii=False,
            )
        )
        started = monotonic()
        while True:
            try:
                submitted = json.loads(
                    input_fn(
                        'JSON: {"decision":"accept|reject|manual_review", '
                        '"changes":{"finding_key":{"field":value}}}: '
                    )
                )
                if not isinstance(submitted, dict) or set(submitted) - {
                    "decision",
                    "changes",
                    "rationale",
                }:
                    raise ValueError("invalid review submission")
                decision = submitted.get("decision")
                if decision not in {"accept", "reject", "manual_review"}:
                    raise ValueError("invalid decision")
                changes = submitted.get("changes", {})
                if not isinstance(changes, dict):
                    raise ValueError("invalid changes")
                revised = json.loads(json.dumps(draft))
                by_key = {item["finding_key"]: item for item in revised["findings"]}
                changed = set()
                changed_count = 0
                for finding_key, fields in changes.items():
                    if (
                        finding_key not in by_key
                        or not isinstance(fields, dict)
                        or not set(fields) <= REVIEW_FIELDS
                    ):
                        raise ValueError("immutable or unknown draft fields")
                    for name, value in fields.items():
                        if by_key[finding_key][name] != value:
                            changed.add(name)
                            changed_count += 1
                            by_key[finding_key][name] = value
                outcome = validate_response(case, json.dumps(revised))
                if outcome.error_type is not None:
                    raise ValueError("replacement failed support validation")
                elapsed = max(0.0, monotonic() - started)
                review = ReviewRecord(
                    case_id=case.case_id,
                    decision=decision,
                    changed_fields=sorted(changed),
                    changed_field_count=changed_count,
                    review_duration_seconds=elapsed,
                    validation_status="valid",
                )
                append_journal(path / "reviews.jsonl", review.model_dump(mode="json"))
                break
            except (ValueError, TypeError, KeyError):
                display("Invalid decision or replacement draft; please retry.")
    return recompute_run(path)[1]


async def check_connection(
    config: Mapping[str, Any], *, client: CompletionClient
) -> dict[str, object]:
    """One isolated connectivity request; no evaluation case or repair loop."""
    requested_at = datetime.now(UTC)
    response = await client.complete_once(
        [{"role": "user", "content": 'Return only this JSON object: {"findings": []}'}]
    )
    attempt = AttemptRecord(
        case_id="connectivity-check",
        attempt_number=1,
        request_kind="original",
        requested_at=requested_at,
        pricing_tier=pricing_tier(requested_at, config),
        **response.model_dump(),
    )
    cost = estimate_cost([attempt], config)
    return {
        "status": attempt.status,
        "base_url": config["base_url"],
        "requested_model": attempt.requested_model,
        "returned_model": attempt.returned_model,
        "system_fingerprint": attempt.system_fingerprint,
        "http_status": attempt.http_status,
        "error_category": attempt.error_category,
        "requested_at": requested_at.isoformat(),
        "pricing_tier": attempt.pricing_tier,
        "usage": {
            name: getattr(attempt, name)
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_hit_tokens",
                "cache_miss_tokens",
                "total_tokens",
            )
        },
        "latency_ms": attempt.latency_ms,
        "conservative_estimated_cost_usd": str(cost) if cost is not None else None,
    }


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Frozen real-model challenge evaluation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "baseline", "analyze", "improved", "compare", "review"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--output", required=command != "review", type=Path)
        if command == "analyze":
            sub.add_argument(
                "--baseline", "--run", dest="run", required=True, type=Path
            )
        if command == "review":
            sub.add_argument("--run", required=True, type=Path)
        if command in {"check", "baseline", "improved"}:
            sub.add_argument("--config", type=Path, default=Path(CONFIG))
        if command in {"baseline", "improved"}:
            sub.add_argument("--dataset", type=Path, default=Path(DATASET))
        if command in {"improved", "compare"}:
            sub.add_argument("--baseline", required=True, type=Path)
        if command == "improved":
            sub.add_argument("--analysis", required=True, type=Path)
        if command == "compare":
            sub.add_argument("--improved", required=True, type=Path)
            sub.add_argument("--analysis", required=True, type=Path)
    args = parser.parse_args(argv)
    root = root or Path(__file__).resolve().parents[5]
    try:
        if args.command == "analyze":
            exclusive_output(args.output, analyze_run(args.run).model_dump(mode="json"))
        elif args.command == "review":
            if args.output is not None and args.output.exists():
                raise ValueError("output already exists")
            reviewed = review_cases(args.run)
            if args.output is not None:
                exclusive_output(
                    args.output,
                    {"cases": [x.model_dump(mode="json") for x in reviewed]},
                )
        elif args.command == "compare":
            if args.analysis is None:
                raise ValueError("compare requires --analysis")
            from tracerisk.challenge_eval.reporting import generate_reports

            generate_reports(
                args.baseline,
                args.improved,
                args.analysis,
                args.output,
            )
        elif args.command == "check":
            if args.output.exists():
                raise ValueError("output already exists")
            config = json.loads((root / args.config).read_bytes())
            settings = DeepSeekRequestSettings.model_validate(
                {key: config[key] for key in DeepSeekRequestSettings.model_fields}
            )
            client = DeepSeekClient(settings=settings, api_key=load_api_key(root))
            result = asyncio.run(check_connection(config, client=client))
            exclusive_output(args.output, result)
            print(canonical(result).decode("utf-8"))
        else:
            config_path = root / args.config
            dataset_path = root / args.dataset
            config = json.loads(config_path.read_bytes())
            dataset = load_cases(dataset_path)
            validate_frozen_shape(dataset)
            dimension = None
            if args.command == "improved":
                analysis = ImprovementAnalysis.model_validate_json(
                    args.analysis.read_bytes()
                )
                dimension = (
                    analysis.selected_problem.changed_dimension
                    if analysis.selected_problem
                    else None
                )
            manifest = build_manifest(
                root,
                phase="improved" if args.command == "improved" else "baseline",
                run_id=args.output.name,
                changed_dimension=dimension,
                dataset_path=dataset_path,
                config_path=config_path,
            )
            if args.command == "improved":
                verify_improvement(args.baseline, args.analysis, manifest)
            key = load_api_key(root)
            manifest = prepare_run(args.output, manifest)
            snapshots = [(dataset_path, "dataset.jsonl"), (config_path, "config.json")]
            for source, target in snapshots:
                destination = args.output / target
                raw = source.read_bytes()
                if destination.exists() and destination.read_bytes() != raw:
                    raise ValueError("run snapshot differs")
                if not destination.exists():
                    write_bytes(destination, raw)
            settings = DeepSeekRequestSettings.model_validate(
                {key: config[key] for key in DeepSeekRequestSettings.model_fields}
            )
            client = DeepSeekClient(settings=settings, api_key=key)
            asyncio.run(
                run_cases(
                    args.output,
                    dataset.cases,
                    config,
                    client=client,
                )
            )
            manifest.status = "completed"
            write_json(args.output / "manifest.json", manifest.model_dump(mode="json"))
    except (ValueError, OSError) as exc:
        # Exceptions here contain metadata only, never provider bodies or secrets.
        parser.exit(2, f"Evaluation failed: {exc}\n")
    return 0


def exclusive_output(path: Path, value: object) -> None:
    if path.exists():
        raise ValueError("output already exists")
    write_json(path, value)
