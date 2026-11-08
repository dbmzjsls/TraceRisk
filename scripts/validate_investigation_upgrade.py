"""Replay historical responses or run a frozen independent validation set.

Historical replay never performs HTTP or changes the original journals. Live
validation reuses the existing audited request budget and production validator.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from tracerisk.challenge_eval.client import DeepSeekClient, DeepSeekRequestSettings
from tracerisk.challenge_eval.dataset import load_cases
from tracerisk.challenge_eval.runner import (
    build_manifest,
    load_api_key,
    prepare_run,
    run_cases,
    write_bytes,
    write_json,
)
from tracerisk.challenge_eval.validation import validate_response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["replay", "live"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must not already exist; historical records stay immutable")
    if args.mode == "replay":
        source = ROOT / "docs/evaluation/model-challenge/improved"
        dataset = load_cases(source / "dataset.jsonl")
        by_id = {case.case_id: case for case in dataset.cases}
        raw = (source / "cases.jsonl").read_bytes()
        records = []
        for line in raw.splitlines():
            old = json.loads(line)
            case = by_id[old["case_id"]]
            last = old["attempts"][-1]
            current = (
                validate_response(case, last["response_content"] or "")
                if last["status"] == "succeeded"
                else None
            )
            records.append(
                {
                    "case_id": case.case_id,
                    "expected": case.expected_action,
                    "historical": old["actual_action"],
                    "replayed": current.actual_action if current else None,
                    "error_type": current.error_type
                    if current
                    else last["error_category"],
                }
            )

        def counts(field: str) -> dict[str, int]:
            return {
                "missed_review": sum(
                    r["expected"] == "manual_review" and r[field] != "manual_review"
                    for r in records
                ),
                "required_review_routed": sum(
                    r["expected"] == "manual_review" and r[field] == "manual_review"
                    for r in records
                ),
                "unnecessary_review": sum(
                    r["expected"] != "manual_review" and r[field] == "manual_review"
                    for r in records
                ),
            }

        write_json(
            args.output / "replay.json",
            {
                "mode": "offline_replay_no_new_model_calls",
                "input_sha256": sha256(raw).hexdigest(),
                "dataset_sha256": dataset.sha256,
                "before": counts("historical"),
                "after": counts("replayed"),
                "records": records,
            },
        )
        return

    dataset_path = ROOT / "datasets/evaluation/investigation_validation_cases.jsonl"
    config_path = ROOT / "datasets/evaluation/model_challenge_config.json"
    dataset = load_cases(dataset_path)
    if len(dataset.cases) != 20 or set(
        Counter(c.category for c in dataset.cases).values()
    ) != {5}:
        parser.error("independent dataset must contain 20 cases, five per category")
    config = json.loads(config_path.read_bytes())
    manifest = build_manifest(
        ROOT,
        phase="baseline",
        run_id=args.output.name,
        dataset_path=dataset_path,
        config_path=config_path,
    )
    # Include this entry point, which is outside the original challenge surface.
    script_hash = sha256(Path(__file__).read_bytes()).hexdigest()
    key = load_api_key(ROOT)
    prepare_run(args.output, manifest)
    write_bytes(args.output / "dataset.jsonl", dataset_path.read_bytes())
    write_bytes(args.output / "config.json", config_path.read_bytes())
    settings = DeepSeekRequestSettings.model_validate(
        {field: config[field] for field in DeepSeekRequestSettings.model_fields}
    )
    results = asyncio.run(
        run_cases(
            args.output,
            dataset.cases,
            config,
            client=DeepSeekClient(settings=settings, api_key=key),
        )
    )
    events = [
        json.loads(line)
        for line in (args.output / "case-events.jsonl").read_bytes().splitlines()
    ]
    final_validations = [event["validations"][-1] for event in events]
    http_ok = [v for v in final_validations if v is not None]
    required = sum(c.expected_action == "manual_review" for c in dataset.cases)
    required_routed = sum(r.correct_manual_review_count for r in results)
    estimates = [r.conservative_estimated_cost_usd for r in results]
    summary = {
        "mode": "real_model_independent_synthetic_validation",
        "dataset_sha256": dataset.sha256,
        "entrypoint_sha256": script_hash,
        "case_count": len(results),
        "http_attempt_count": sum(r.http_attempt_count for r in results),
        "structured_output_success": {
            "numerator": sum(v["error_type"] != "structure_error" for v in http_ok),
            "denominator": len(results),
        },
        "citation_legality": {
            "numerator": sum(
                v["error_type"] not in {"citation_error", "structure_error"}
                for v in http_ok
            ),
            "denominator": len(results),
            "note": "final responses passing structure and citation boundaries; empty findings pass vacuously, not evidence completeness",
        },
        "review_recall": {"numerator": required_routed, "denominator": required},
        "missed_review": sum(r.missed_review_count for r in results),
        "required_review_not_routed": required - required_routed,
        "unnecessary_review": sum(r.unnecessary_review_count for r in results),
        "not_required_review_cases": len(results) - required,
        "actions": dict(Counter(str(r.actual_action) for r in results)),
        "execution_failures": dict(
            Counter(e for r in results for e in r.execution_error_categories)
        ),
        "validation_failures": dict(
            Counter(e for r in results for e in r.validation_errors)
        ),
        "total_model_latency_ms": sum(r.total_model_latency_ms for r in results),
        "conservative_estimated_cost_usd": str(sum(estimates))
        if all(v is not None for v in estimates)
        else None,
        "billing_cost_usd": None,
        "cost_note": "usage-based estimate using the frozen 2026-10-23 price snapshot; actual invoice unavailable",
        "human_review": "not_measured",
        "label_limit": "developer-authored synthetic labels; no team or domain-expert agreement study",
    }
    write_json(args.output / "summary.json", summary)
    manifest.status = "completed"
    write_json(args.output / "manifest.json", manifest.model_dump(mode="json"))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
