"""Opt-in real-model API/queue/review/report validation on a newly owned DB."""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "apps/api/src"), str(ROOT / "scripts")]

import httpx
from database_lifecycle import (
    create_owned_database,
    drop_owned_database,
    initialize_target,
    retained_index,
)
from sqlalchemy import select
from tracerisk.ai.models import ModelInvocation
from tracerisk.auth.models import User
from tracerisk.auth.service import create_access_token
from tracerisk.challenge_eval.runner import load_api_key
from tracerisk.config import Settings
from tracerisk.main import app
from tracerisk.workflows.service import serve


async def validate(output: Path) -> dict[str, object]:
    results: dict[str, object] = {}
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            users = (await session.scalars(select(User))).all()
        secret = Settings().jwt_secret
        assert secret is not None
        headers = {
            user.username: {
                "Authorization": "Bearer "
                + create_access_token(user.id, secret.get_secret_value())
            }
            for user in users
            if user.username in {"analyst", "reviewer"}
        }
        worker = asyncio.create_task(serve())
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://isolated"
            ) as client:
                response = await client.get("/companies", headers=headers["analyst"])
                response.raise_for_status()
                company = next(
                    c
                    for c in response.json()["items"]
                    if c["unified_credit_code"] == "SYNTHETIC-COMPANY-003"
                )
                started_at = time.monotonic()
                created = await client.post(
                    "/investigations",
                    headers={
                        **headers["analyst"],
                        "Idempotency-Key": "real-validation-start",
                    },
                    json={"company_id": company["id"], "as_of_date": "2026-09-11"},
                )
                created.raise_for_status()
                investigation_id = created.json()["id"]

                async def wait_terminal_or_review() -> dict:
                    for _ in range(150):
                        if worker.done():
                            worker.result()
                            raise RuntimeError("worker stopped")
                        response = await client.get(
                            f"/investigations/{investigation_id}",
                            headers=headers["analyst"],
                        )
                        response.raise_for_status()
                        detail = response.json()
                        if detail["current_run"]["status"] in {
                            "awaiting_review",
                            "completed",
                            "failed",
                        }:
                            return detail
                        await asyncio.sleep(1)
                    raise RuntimeError("real workflow timed out")

                detail = await wait_terminal_or_review()
                results["status_before_review"] = detail["current_run"]["status"]
                results["review_reasons"] = detail["review_reasons"]
                results["model_execution"] = detail["model_execution"]
                results["rule_score"] = detail["rule_score"]
                if detail["current_run"]["status"] == "awaiting_review":
                    if detail["ai_findings"]:
                        for index, finding in enumerate(detail["ai_findings"]):
                            response = await client.post(
                                f"/reviews/{finding['finding_id']}",
                                headers={
                                    **headers["reviewer"],
                                    "Idempotency-Key": f"real-review-{index}",
                                },
                                json={
                                    "finding_version": finding["finding_version"],
                                    "decision": "approve",
                                    "reason": "自动化合成链路验证；不是业务人工核验",
                                },
                            )
                            response.raise_for_status()
                    else:
                        response = await client.post(
                            f"/runs/{detail['current_run']['id']}/review",
                            headers={
                                **headers["reviewer"],
                                "Idempotency-Key": "real-run-review",
                            },
                            json={
                                "decision": "approve",
                                "reason": "自动化合成链路验证；不是业务人工核验",
                            },
                        )
                        response.raise_for_status()
                    detail = await wait_terminal_or_review()
                results["final_status"] = detail["current_run"]["status"]
                results["failure_code"] = detail["failure_code"]
                results["report_created"] = (
                    detail["current_run"]["report_id"] is not None
                )
                results["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
                results["human_review"] = (
                    "automated protocol submission; human quality/time not measured"
                )
                if detail["current_run"]["report_id"]:
                    response = await client.get(
                        f"/reports/{detail['current_run']['report_id']}",
                        headers=headers["analyst"],
                    )
                    response.raise_for_status()
                    report = response.json()
                    (output / "report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    assert report["snapshot"]["rule_score"] == 95
                    assert report["snapshot"]["model_execution"]["mode"] == "real"
                async with app.state.session_factory() as session:
                    from uuid import UUID

                    attempts = (
                        await session.scalars(
                            select(ModelInvocation).where(
                                ModelInvocation.run_id
                                == UUID(detail["current_run"]["id"])
                            )
                        )
                    ).all()
                results["attempts"] = [
                    {
                        "model": a.model,
                        "status": a.status,
                        "latency_ms": a.latency_ms,
                        "input_tokens": a.input_tokens,
                        "output_tokens": a.output_tokens,
                        "error_category": a.error_category,
                    }
                    for a in attempts
                ]
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must not exist")
    config = json.loads(
        (ROOT / "datasets/evaluation/model_challenge_config.json").read_bytes()
    )
    key = load_api_key(ROOT)
    os.environ.update(
        {
            "MODEL_ADAPTER": "mock",
            "EMBEDDING_PROVIDER": "fake",
            "EMBEDDING_MODEL": "fake-sha256-v1",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }
    )
    settings = Settings()
    retained_index(settings)
    owned = create_owned_database(settings, purpose="eval", run_id=uuid4().hex)
    args.output.mkdir(parents=True)
    result: dict[str, object] = {
        "database": owned.name,
        "mode": "real-model-api-queue-workflow",
    }
    try:
        initialize_target(settings, owned.name)
        os.environ.update(
            {
                "POSTGRES_DB": owned.name,
                "MODEL_ADAPTER": "openai_compatible",
                "MODEL_BASE_URL": config["base_url"],
                "MODEL_NAME": config["model"],
                "MODEL_API_KEY": key.get_secret_value(),
                "JWT_SECRET": "isolated-real-workflow-validation-secret-at-least-48-characters",
                "WORKER_HEALTH_FILE": str(args.output / "worker-health.json"),
            }
        )
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result.update(runner.run(validate(args.output)))
    except Exception as exc:  # noqa: BLE001 - redact provider/DB details in audit output
        # Do not serialize provider bodies, connection strings or credentials.
        result.update(status="failed", error_type=type(exc).__name__)
    finally:
        os.environ.pop("MODEL_API_KEY", None)
        drop_owned_database(settings, owned)
        retained_index(settings)
        result["owned_database_cleaned"] = True
        (args.output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False))
    if result.get("final_status") != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
