"""Redeliver one completed Run's resume Outbox on its recorded E2E database."""

import argparse
import json
from pathlib import Path
from uuid import UUID

import psycopg
from database_lifecycle import _url
from seed_e2e import owned_from_record
from tracerisk.config import Settings


def replay_ready(jobs: list[tuple[str]]) -> bool:
    return jobs == [("succeeded",)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    args = parser.parse_args()
    owned = owned_from_record(args.record)
    settings = Settings()
    if settings.postgres_db != owned.name:
        raise ValueError("Configured database differs from owned E2E database")
    with psycopg.connect(_url(settings, owned.name)) as connection:
        identity = connection.execute("SELECT current_database()").fetchone()
        if identity is None or identity[0] != owned.name:
            raise RuntimeError("Connected to the wrong database")
        run = connection.execute(
            "SELECT status FROM workflow_runs WHERE id = %s FOR UPDATE", (args.run_id,)
        ).fetchone()
        if run != ("completed",):
            raise ValueError("Replay requires a completed Run")
        jobs = connection.execute(
            "SELECT status FROM procrastinate_jobs "
            "WHERE queueing_lock = %s ORDER BY id",
            (f"run:{args.run_id}:resume",),
        ).fetchall()
        if not replay_ready(jobs):
            raise ValueError(
                "Replay requires exactly one successfully consumed resume job"
            )
        changed = connection.execute(
            "UPDATE dispatch_outbox SET dispatched_at = NULL "
            "WHERE run_id = %s AND operation = 'resume' AND dispatched_at IS NOT NULL "
            "RETURNING id",
            (args.run_id,),
        ).fetchall()
        if len(changed) != 1:
            raise ValueError("Replay requires exactly one dispatched resume Outbox")
        connection.commit()
    print(json.dumps({"run_id": str(args.run_id), "resume_redelivered": 1}))


if __name__ == "__main__":
    main()
