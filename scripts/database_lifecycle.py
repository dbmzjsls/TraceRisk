"""Guarded lifecycle for explicitly provisioned TraceRisk databases."""

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, LiteralString

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tracerisk.config import Settings

Purpose = Literal["eval", "e2e"]
_GENERATED = re.compile(r"^tracerisk_(eval|e2e)_[0-9a-f]{32}$")

_QUEUE_SCHEMA_STATE_QUERY: LiteralString = """
SELECT
    (
        EXISTS (
            SELECT 1
            FROM pg_class
            WHERE relnamespace = current_schema()::regnamespace
              AND left(relname, length('procrastinate_')) = 'procrastinate_'
        )
        OR EXISTS (
            SELECT 1
            FROM pg_type
            WHERE typnamespace = current_schema()::regnamespace
              AND left(typname, length('procrastinate_')) = 'procrastinate_'
        )
        OR EXISTS (
            SELECT 1
            FROM pg_proc
            WHERE pronamespace = current_schema()::regnamespace
              AND left(proname, length('procrastinate_')) = 'procrastinate_'
        )
    ) AS present,
    (
        to_regclass('procrastinate_jobs') IS NOT NULL
        AND to_regclass('procrastinate_workers') IS NOT NULL
        AND to_regclass('procrastinate_events') IS NOT NULL
        AND to_regclass('procrastinate_periodic_defers') IS NOT NULL
        AND to_regtype('procrastinate_job_status') IS NOT NULL
        AND to_regtype('procrastinate_job_event_type') IS NOT NULL
        AND to_regtype('procrastinate_job_to_defer_v1') IS NOT NULL
        AND to_regprocedure(
            'procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[])'
        ) IS NOT NULL
        AND to_regprocedure(
            'procrastinate_fetch_job_v2(character varying[],bigint)'
        ) IS NOT NULL
        AND to_regprocedure(
            'procrastinate_finish_job_v1(bigint,procrastinate_job_status,boolean)'
        ) IS NOT NULL
        AND to_regprocedure(
            'procrastinate_retry_job_v2(bigint,timestamp with time zone,integer,'
            'character varying,character varying)'
        ) IS NOT NULL
        AND to_regprocedure('procrastinate_register_worker_v1()') IS NOT NULL
        AND to_regprocedure('procrastinate_unregister_worker_v1(bigint)') IS NOT NULL
        AND to_regprocedure('procrastinate_update_heartbeat_v1(bigint)') IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM (
                VALUES
                    ('id'),
                    ('queue_name'),
                    ('task_name'),
                    ('priority'),
                    ('lock'),
                    ('queueing_lock'),
                    ('args'),
                    ('status'),
                    ('scheduled_at'),
                    ('attempts'),
                    ('abort_requested'),
                    ('worker_id')
            ) AS required(column_name)
            WHERE NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'procrastinate_jobs'
                  AND columns.column_name = required.column_name
            )
        )
    ) AS compatible
"""


def application_head() -> str:
    config = Config(str(Path(__file__).resolve().parents[1] / "apps/api/alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("Application migrations require exactly one Alembic head")
    return heads[0]


@dataclass(frozen=True)
class OwnedDatabase:
    name: str
    purpose: Purpose
    run_id: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", self.run_id):
            raise ValueError("Run ID must be 32 lowercase hex characters")
        if self.name != f"tracerisk_{self.purpose}_{self.run_id}":
            raise ValueError("Database name does not match this run and purpose")


def _url(settings: Settings, database: str) -> str:
    return settings.database_url.set(
        drivername="postgresql", database=database
    ).render_as_string(hide_password=False)


def _admin(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(_url(settings, "postgres"), autocommit=True)


def current_database(settings: Settings, database: str) -> str:
    with psycopg.connect(_url(settings, database)) as connection:
        row = connection.execute("SELECT current_database()").fetchone()
        if row is None:
            raise RuntimeError("Database identity query returned no row")
        return str(row[0])


def create_owned_database(
    settings: Settings, *, purpose: Purpose, run_id: str
) -> OwnedDatabase:
    owned = OwnedDatabase(f"tracerisk_{purpose}_{run_id}", purpose, run_id)
    with _admin(settings) as connection:
        if connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (owned.name,)
        ).fetchone():
            raise ValueError("Generated database already exists; ownership is unknown")
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(owned.name))
        )
    # Return ownership before any second connection can fail. The caller verifies
    # identity after assigning this handle and drops only this exact target.
    return owned


def drop_owned_database(settings: Settings, owned: OwnedDatabase) -> None:
    # Revalidate even if a caller bypassed the frozen dataclass constructor.
    if not _GENERATED.fullmatch(owned.name) or owned.name != (
        f"tracerisk_{owned.purpose}_{owned.run_id}"
    ):
        raise ValueError("Refusing to drop a database without exact ownership")
    with _admin(settings) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(owned.name))
        )


def ensure_demo_database(settings: Settings) -> str:
    name = "tracerisk_demo"
    with _admin(settings) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone():
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            )
    if current_database(settings, name) != name:
        raise RuntimeError("Connected database differs from demo target")
    return name


def _require_fake(settings: Settings) -> None:
    if (
        settings.model_adapter != "mock"
        or settings.embedding_provider != "fake"
        or settings.embedding_model != "fake-sha256-v1"
    ):
        raise ValueError("Lifecycle initialization requires Mock/Fake settings")


def _require_target(target: str) -> None:
    if target != "tracerisk_demo" and not _GENERATED.fullmatch(target):
        raise ValueError("Refusing a database outside the demo/generated targets")


def _target_settings(settings: Settings, target: str) -> Settings:
    _require_target(target)
    result = settings.model_copy(update={"postgres_db": target})
    if current_database(result, target) != target:
        raise RuntimeError("Connected database differs from lifecycle target")
    return result


def _reject_non_fake_index(settings: Settings, target: str) -> None:
    with psycopg.connect(_url(settings, target)) as connection:
        connection.execute("BEGIN READ ONLY")
        table = connection.execute(
            "SELECT to_regclass('embedding_index_versions')"
        ).fetchone()
        if table is not None and table[0] is not None:
            active = connection.execute(
                "SELECT provider, model FROM embedding_index_versions WHERE active"
            ).fetchone()
            if active is not None and active != ("fake", "fake-sha256-v1"):
                raise RuntimeError("Existing target has a non-Fake active index")
        connection.rollback()


async def _initialize_async(settings: Settings) -> None:
    from tracerisk.auth.seed import seed_accounts
    from tracerisk.retrieval.embeddings import FakeEmbedding
    from tracerisk.retrieval.indexing import IndexContract, build_index
    from tracerisk.synthetic.generator import generate_catalog
    from tracerisk.synthetic.importer import import_catalog
    from tracerisk.workflows.queue import create_queue
    from tracerisk.workflows.spike_graph import initialize_checkpoints

    async with create_queue(settings).open_async() as app:
        queue_schema = await app.connector.execute_query_one_async(
            query=_QUEUE_SCHEMA_STATE_QUERY
        )
        if not queue_schema["compatible"]:
            if queue_schema["present"]:
                raise RuntimeError(
                    "Existing queue schema is incomplete or incompatible"
                )
            await app.schema_manager.apply_schema_async()
            queue_schema = await app.connector.execute_query_one_async(
                query=_QUEUE_SCHEMA_STATE_QUERY
            )
            if not queue_schema["compatible"]:
                raise RuntimeError("Queue schema installation is incompatible")
    await initialize_checkpoints()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            await seed_accounts(session)
        async with sessions.begin() as session:
            await import_catalog(session, generate_catalog())
            await build_index(session, FakeEmbedding(), IndexContract.fake())
    finally:
        await engine.dispose()


def initialize_target(settings: Settings, target: str) -> None:
    """Idempotently finish an explicitly provisioned Fake target."""
    _require_fake(settings)
    target_settings = _target_settings(settings, target)
    _reject_non_fake_index(target_settings, target)
    from alembic import command

    prior = os.environ.get("POSTGRES_DB")
    os.environ["POSTGRES_DB"] = target
    try:
        config = Config(
            str(Path(__file__).resolve().parents[1] / "apps/api/alembic.ini")
        )
        command.upgrade(config, "head")
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            runner.run(_initialize_async(_target_settings(settings, target)))
        verify_target(settings, target)
    finally:
        if prior is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = prior


def verify_target(settings: Settings, target: str) -> dict[str, str]:
    """Read-only readiness check for daily start; never seeds or indexes."""
    _require_fake(settings)
    expected_revision = application_head()
    target_settings = _target_settings(settings, target)
    with psycopg.connect(_url(target_settings, target)) as connection:
        connection.execute("BEGIN READ ONLY")
        revisions = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        if revisions != [(expected_revision,)]:
            raise RuntimeError("Target Alembic revision is incomplete")
        required = (
            "procrastinate_jobs",
            "checkpoints",
            "checkpoint_blobs",
            "users",
            "companies",
            "embedding_index_versions",
        )
        for table in required:
            if connection.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (
                None,
            ):
                raise RuntimeError(
                    f"Target framework or application schema missing: {table}"
                )
        index = connection.execute(
            "SELECT provider, model FROM embedding_index_versions WHERE active"
        ).fetchone()
        if index != ("fake", "fake-sha256-v1"):
            raise RuntimeError("Target active index is not Fake")
        for table in ("users", "companies"):
            count = connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()
            if count is None or count[0] == 0:
                raise RuntimeError(f"Target seed is missing: {table}")
        connection.rollback()
    return {"database": target, "alembic_revision": expected_revision, "index": "fake"}


def retained_index(settings: Settings) -> dict[str, str]:
    """Inspect the existing retained index without creating or writing its DB."""
    with _admin(settings) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'tracerisk'"
        ).fetchone():
            return {"database": "tracerisk", "status": "absent"}
    with psycopg.connect(_url(settings, "tracerisk")) as connection:
        connection.execute("BEGIN READ ONLY")
        index = connection.execute(
            "SELECT provider, model FROM embedding_index_versions WHERE active"
        ).fetchone()
        connection.rollback()
    if index != ("bge", "BAAI/bge-small-zh-v1.5"):
        raise RuntimeError("Retained active index differs from the expected BGE index")
    return {"database": "tracerisk", "provider": str(index[0]), "model": str(index[1])}


def _write_record(path: Path, owned: OwnedDatabase) -> None:
    with path.open("x", encoding="utf-8") as record:
        json.dump(
            {"name": owned.name, "purpose": owned.purpose, "run_id": owned.run_id},
            record,
        )


def _read_record(path: Path) -> OwnedDatabase:
    data = json.loads(path.read_text(encoding="utf-8"))
    return OwnedDatabase(data["name"], data["purpose"], data["run_id"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify-retained")
    subparsers.add_parser("bootstrap-demo")
    generated = subparsers.add_parser("bootstrap-e2e")
    generated.add_argument("--record", type=Path, required=True)
    generated.add_argument("--run-id", required=True)
    verify = subparsers.add_parser("verify-target")
    verify.add_argument("--target", required=True)
    drop = subparsers.add_parser("drop-owned")
    drop.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "verify-retained":
        result = retained_index(settings)
    elif args.command == "bootstrap-demo":
        name = ensure_demo_database(settings)
        initialize_target(settings, name)
        result = verify_target(settings, name)
    elif args.command == "bootstrap-e2e":
        if args.record.exists():
            raise ValueError("Ownership record already exists")
        owned = create_owned_database(settings, purpose="e2e", run_id=args.run_id)
        try:
            _write_record(args.record, owned)
        except BaseException:
            drop_owned_database(settings, owned)
            raise
        initialize_target(settings, owned.name)
        result = verify_target(settings, owned.name)
    elif args.command == "verify-target":
        result = verify_target(settings, args.target)
    else:
        owned = _read_record(args.record)
        if owned.purpose != "e2e":
            raise ValueError("This command may clean up only an owned E2E database")
        drop_owned_database(settings, owned)
        args.record.unlink()
        result = {"database": owned.name, "status": "dropped"}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()


def retained_snapshot(settings: Settings) -> dict[str, object]:
    """Read-only canonical inventory; passwords and row values never leave the DB."""
    if settings.postgres_db != "tracerisk":
        raise ValueError("Retained baseline requires database tracerisk")
    with psycopg.connect(_url(settings, "tracerisk")) as connection:
        connection.execute("BEGIN READ ONLY")
        identity = connection.execute("SELECT current_database()").fetchone()
        if identity is None or identity[0] != "tracerisk":
            raise RuntimeError("Retained connection targets another database")
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        if revision is None:
            raise RuntimeError("Retained database has no Alembic revision")
        tables = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version' ORDER BY tablename"
        ).fetchall()
        inventory: dict[str, object] = {}
        for (table,) in tables:
            columns = connection.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN unnest(i.indkey) WITH ORDINALITY "
                "AS key(attnum, position) ON true "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.attnum "
                "WHERE n.nspname = 'public' AND c.relname = %s AND i.indisprimary "
                "ORDER BY key.position",
                (table,),
            ).fetchall()
            if not columns:
                raise ValueError(f"Table {table} has no primary key")
            pk_columns = [str(column[0]) for column in columns]
            rows = connection.execute(
                sql.SQL("SELECT to_jsonb(t) FROM {} AS t").format(sql.Identifier(table))
            ).fetchall()
            sorted_rows = sorted(
                (row[0] for row in rows),
                key=lambda item: tuple(str(item[column]) for column in pk_columns),
            )
            canonical = json.dumps(
                sorted_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            inventory[str(table)] = {
                "count": len(sorted_rows),
                "primary_key_columns": pk_columns,
                "primary_keys": [
                    [str(row[column]) for column in pk_columns] for row in sorted_rows
                ],
                "canonical_rows_sha256": sha256(canonical).hexdigest(),
            }
        connection.rollback()
    return {
        "database": "tracerisk",
        "alembic_revision": revision[0],
        "tables": inventory,
    }
