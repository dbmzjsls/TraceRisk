"""Create, verify, reseed, and remove only this run's owned E2E database.

Set Mock/Fake/no-tracing environment variables before invoking this script.
The record is the ownership capability; a database without its record is never
adopted or removed by this command.
"""

import argparse
import asyncio
import json
import re
import sys
from hashlib import sha256
from pathlib import Path

import psycopg
from database_lifecycle import (
    OwnedDatabase,
    _url,
    create_owned_database,
    drop_owned_database,
    initialize_target,
    verify_target,
)
from psycopg import sql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tracerisk.config import Settings
from tracerisk.synthetic.generator import generate_catalog

E2E_NAME = re.compile(r"^tracerisk_e2e_[0-9a-f]{32}$")
BUSINESS_TABLES = (
    "users",
    "companies",
    "company_aliases",
    "evidence",
    "source_coverage",
    "evidence_chunks",
    "embedding_index_versions",
    "chunk_embeddings",
    "index_query_embeddings",
)


async def reseed_business(settings: Settings, owned: OwnedDatabase) -> None:
    """Reapply only idempotent seed/index work after full-schema verification.

    Procrastinate's schema installer is a first-install DDL operation, so a
    repeated seed must not rerun it on an already initialized database.
    """
    from tracerisk.auth.seed import seed_accounts
    from tracerisk.retrieval.embeddings import FakeEmbedding
    from tracerisk.retrieval.indexing import IndexContract, build_index
    from tracerisk.synthetic.importer import import_catalog

    target = settings.model_copy(update={"postgres_db": owned.name})
    engine = create_async_engine(target.database_url, pool_pre_ping=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            await seed_accounts(session)
        async with sessions.begin() as session:
            await import_catalog(session, generate_catalog())
            await build_index(session, FakeEmbedding(), IndexContract.fake())
    finally:
        await engine.dispose()


def repeat_seed(settings: Settings, owned: OwnedDatabase) -> None:
    verify_target(settings, owned.name)
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(reseed_business(settings, owned))
    seed_probe(settings, owned)
    verify_target(settings, owned.name)


def probe_username(owned: OwnedDatabase) -> str:
    return f"e2e_probe_{owned.run_id}"


def seed_probe(settings: Settings, owned: OwnedDatabase) -> None:
    """Bind the public API to this exact owned DB without exposing credentials."""
    with psycopg.connect(_url(settings, owned.name)) as connection:
        identity = connection.execute("SELECT current_database()").fetchone()
        if identity != (owned.name,):
            raise RuntimeError("Probe seed connected to the wrong database")
        analyst = connection.execute(
            "SELECT password_hash FROM users WHERE username = 'analyst'"
        ).fetchone()
        if analyst is None:
            raise RuntimeError("Analyst seed account is absent")
        row = connection.execute(
            "INSERT INTO users (id, username, password_hash, role) "
            "SELECT gen_random_uuid(), %s, password_hash, 'analyst' "
            "FROM users WHERE username = 'analyst' "
            "ON CONFLICT (username) DO NOTHING RETURNING username",
            (probe_username(owned),),
        ).fetchone()
        if row is None:
            existing = connection.execute(
                "SELECT password_hash, role FROM users WHERE username = %s",
                (probe_username(owned),),
            ).fetchone()
            if existing != (analyst[0], "analyst"):
                raise RuntimeError("Owned API probe account has an unexpected hash")
        connection.commit()


def owned_from_record(path: Path) -> OwnedDatabase:
    data = json.loads(path.read_text(encoding="utf-8"))
    owned = OwnedDatabase(data["name"], data["purpose"], data["run_id"])
    if owned.purpose != "e2e" or not E2E_NAME.fullmatch(owned.name):
        raise ValueError("Only a recorded tracerisk_e2e_<32 lowercase hex> is allowed")
    return owned


def snapshot(settings: Settings, owned: OwnedDatabase) -> dict[str, dict[str, object]]:
    """Return counts and row hashes, never row contents or credentials."""
    result: dict[str, dict[str, object]] = {}
    with psycopg.connect(_url(settings, owned.name)) as connection:
        connection.execute("BEGIN READ ONLY")
        identity = connection.execute("SELECT current_database()").fetchone()
        if identity is None or identity[0] != owned.name:
            raise RuntimeError("Read-only snapshot connected to a different database")
        for table in BUSINESS_TABLES:
            rows = connection.execute(
                sql.SQL("SELECT to_jsonb(t)::text FROM {} AS t").format(
                    sql.Identifier(table)
                )
            ).fetchall()
            canonical = "\n".join(sorted(row[0] for row in rows)).encode("utf-8")
            result[table] = {
                "count": len(rows),
                "sha256": sha256(canonical).hexdigest(),
            }
        connection.rollback()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "reseed", "verify", "drop"))
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--run-id", help="32 lowercase hex digits; required for create")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "create":
        if args.run_id is None or not re.fullmatch(r"[0-9a-f]{32}", args.run_id):
            raise ValueError("Create requires a 32-lowercase-hex run ID")
        if args.record.exists():
            raise ValueError("Ownership record already exists")
        owned = create_owned_database(settings, purpose="e2e", run_id=args.run_id)
        try:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            with args.record.open("x", encoding="utf-8") as record:
                json.dump(
                    {
                        "name": owned.name,
                        "purpose": owned.purpose,
                        "run_id": owned.run_id,
                    },
                    record,
                )
        except BaseException:
            drop_owned_database(settings, owned)
            raise
        initialize_target(settings, owned.name)
        seed_probe(settings, owned)
        first = snapshot(settings, owned)
        repeat_seed(settings, owned)
        second = snapshot(settings, owned)
        if first != second:
            raise RuntimeError("A second setup changed E2E business rows or hashes")
    else:
        if args.run_id is not None:
            raise ValueError("Run ID is accepted only by create")
        owned = owned_from_record(args.record)
        if args.command == "drop":
            drop_owned_database(settings, owned)
            args.record.unlink()
            print(json.dumps({"database": owned.name, "status": "dropped"}))
            return
        first = snapshot(settings, owned) if args.command == "reseed" else None
        if args.command == "reseed":
            repeat_seed(settings, owned)
        second = snapshot(settings, owned)
        if first is not None and first != second:
            raise RuntimeError("Reseed changed E2E business rows or hashes")
    ready = verify_target(settings, owned.name)
    print(
        json.dumps(
            {
                **ready,
                "scenarios": [item.key for item in generate_catalog()],
                "business_rows": second,
                "idempotent": args.command in {"create", "reseed"},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
