"""Read-only, whitelist-only database evidence for a single owned E2E Run."""

import argparse
import json
import re
from hashlib import sha256
from uuid import UUID

import psycopg
from database_lifecycle import _url
from tracerisk.config import Settings

E2E_NAME = re.compile(r"^tracerisk_e2e_[0-9a-f]{32}$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    args = parser.parse_args()
    if not E2E_NAME.fullmatch(args.database):
        raise ValueError("Read-only assertion target must be an E2E database")
    settings = Settings()
    if settings.postgres_db != args.database:
        raise ValueError("Configured and asserted E2E databases differ")
    with psycopg.connect(_url(settings, args.database)) as connection:
        connection.execute("BEGIN READ ONLY")
        identity = connection.execute("SELECT current_database()").fetchone()
        if identity is None or identity[0] != args.database:
            raise RuntimeError("Connected to the wrong database")
        run = connection.execute(
            "SELECT status FROM workflow_runs WHERE id = %s", (args.run_id,)
        ).fetchone()
        if run is None:
            raise ValueError("Run is absent")
        rules = connection.execute(
            "SELECT coalesce(sum(score), 0), count(*) "
            "FROM rule_findings WHERE run_id = %s",
            (args.run_id,),
        ).fetchone()
        findings = connection.execute(
            "SELECT id, version, finding_key, title, summary, reasoning, citations, "
            "risk_type, claim_code, evidence_sufficiency, confidence_level, "
            "review_recommended, support_status, support_reason "
            "FROM ai_findings WHERE run_id = %s ORDER BY finding_key",
            (args.run_id,),
        ).fetchall()
        human_versions = connection.execute(
            "SELECT id, review_decision_id, ai_finding_id, finding_version, "
            "version, replacement FROM human_finding_versions "
            "WHERE run_id = %s ORDER BY ai_finding_id, version",
            (args.run_id,),
        ).fetchall()
        decisions = connection.execute(
            "SELECT id, ai_finding_id, finding_version, decision "
            "FROM review_decisions WHERE run_id = %s ORDER BY id",
            (args.run_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT sequence, event_type, event_key FROM workflow_events "
            "WHERE run_id = %s ORDER BY sequence",
            (args.run_id,),
        ).fetchall()
        outbox = connection.execute(
            "SELECT operation, count(*), count(dispatched_at) "
            "FROM dispatch_outbox WHERE run_id = %s "
            "GROUP BY operation ORDER BY operation",
            (args.run_id,),
        ).fetchall()
        resume_jobs = connection.execute(
            "SELECT id, status FROM procrastinate_jobs WHERE queueing_lock = %s "
            "ORDER BY id",
            (f"run:{args.run_id}:resume",),
        ).fetchall()
        reports = connection.execute(
            "SELECT id, snapshot, html_content FROM risk_reports WHERE run_id = %s",
            (args.run_id,),
        ).fetchall()
        connection.rollback()
    report = reports[0] if len(reports) == 1 else None
    snapshot = report[1] if report else {}
    result = {
        "database": args.database,
        "run_id": str(args.run_id),
        "status": run[0],
        "rule_score": int(rules[0]),
        "rule_findings": int(rules[1]),
        "ai_findings": [
            {
                "id": str(row[0]),
                "version": row[1],
                "key": row[2],
                "content_sha256": sha256(
                    json.dumps(row[3:], sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
            }
            for row in findings
        ],
        "decisions": [
            {
                "id": str(row[0]),
                "ai_finding_id": str(row[1]) if row[1] else None,
                "finding_version": row[2],
                "decision": row[3],
            }
            for row in decisions
        ],
        "human_versions": [
            {
                "id": str(row[0]),
                "review_decision_id": str(row[1]),
                "ai_finding_id": str(row[2]),
                "finding_version": row[3],
                "version": row[4],
                "content_sha256": sha256(
                    json.dumps(row[5], sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
            }
            for row in human_versions
        ],
        "events": [
            {"sequence": row[0], "type": row[1], "key": row[2]} for row in events
        ],
        "outbox": {row[0]: row[1] for row in outbox},
        "outbox_dispatched": {row[0]: row[2] for row in outbox},
        "resume_jobs": [{"id": row[0], "status": row[1]} for row in resume_jobs],
        "report_count": len(reports),
        "report_id": str(report[0]) if report else None,
        "report_bytes_sha256": (
            sha256(report[2].encode("utf-8")).hexdigest() if report else None
        ),
        "report_ai_ids": [item["id"] for item in snapshot.get("ai_findings", [])],
        "report_final_ids": [item["id"] for item in snapshot.get("final_findings", [])],
        "report_final_sources": [
            {
                "id": item["id"],
                "source": item.get("source"),
                "original_ai_finding_id": item.get("original_ai_finding_id"),
                "human_version": item.get("human_version"),
            }
            for item in snapshot.get("final_findings", [])
        ],
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
