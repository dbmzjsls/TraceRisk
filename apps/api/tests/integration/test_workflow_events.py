import asyncio
from datetime import date
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.investigations.models import Investigation, WorkflowEvent, WorkflowRun
from tracerisk.workflows.events import (
    MAX_EVENT_BATCH_SIZE,
    WorkflowEventMetadataError,
    WorkflowRunNotFound,
    append_workflow_event,
    read_workflow_events,
)

pytestmark = pytest.mark.anyio


async def _seed_run(
    sessions: async_sessionmaker[AsyncSession], *, status: str = "queued"
) -> UUID:
    company_id, user_id, investigation_id, run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    async with sessions.begin() as session:
        session.add_all(
            [
                Company(
                    id=company_id,
                    synthetic_key=f"workflow-event-{company_id}",
                    name="Workflow event company",
                    unified_credit_code=str(company_id),
                ),
                User(
                    id=user_id,
                    username=f"workflow-event-{user_id}",
                    password_hash="unused",
                    role="analyst",
                ),
                Investigation(
                    id=investigation_id,
                    company_id=company_id,
                    created_by=user_id,
                    as_of_date=date(2026, 9, 19),
                    current_run_id=run_id,
                ),
                WorkflowRun(
                    id=run_id,
                    investigation_id=investigation_id,
                    run_number=1,
                    status=status,
                ),
            ]
        )
    return run_id


async def _snapshot_existing_tables(
    sessions: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    excluded = {
        "alembic_version",
        "workflow_events",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
    async with sessions() as session:
        table_names = (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                )
            )
        ).scalars()
        snapshots: dict[str, str] = {}
        for table_name in table_names:
            if table_name in excluded or table_name.startswith("procrastinate_"):
                continue
            assert table_name.replace("_", "").isalnum()
            snapshots[table_name] = str(
                await session.scalar(
                    text(
                        "SELECT COALESCE(jsonb_agg(row ORDER BY row::text), "
                        "'[]')::text "
                        f"FROM (SELECT to_jsonb(t) AS row FROM {table_name} AS t) rows"
                    )
                )
            )
        return snapshots


async def test_0011_migration_schema_and_no_backfill(database: Settings) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.downgrade(config, "0010")
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        legacy_run_id = await _seed_run(sessions, status="completed")
        before = await _snapshot_existing_tables(sessions)

        command.upgrade(config, "0011")

        assert await _snapshot_existing_tables(sessions) == before
        async with sessions() as session:
            columns = dict(
                cast(
                    list[tuple[str, str]],
                    (
                        await session.execute(
                            text(
                                "SELECT column_name, data_type "
                                "FROM information_schema.columns "
                                "WHERE table_schema = 'public' "
                                "AND table_name = 'workflow_events'"
                            )
                        )
                    ).all(),
                )
            )
            assert columns == {
                "id": "uuid",
                "run_id": "uuid",
                "sequence": "integer",
                "event_key": "text",
                "event_type": "text",
                "node_name": "text",
                "run_status": "text",
                "metadata": "jsonb",
                "created_at": "timestamp with time zone",
            }
            constraints = dict(
                cast(
                    list[tuple[str, str]],
                    (
                        await session.execute(
                            text(
                                "SELECT conname, pg_get_constraintdef(oid) "
                                "FROM pg_constraint "
                                "WHERE conrelid = 'workflow_events'::regclass"
                            )
                        )
                    ).all(),
                )
            )
            assert "sequence > 0" in constraints["ck_workflow_event_sequence"]
            assert "event_key" in constraints["ck_workflow_event_key"]
            assert "event_type" in constraints["ck_workflow_event_type"]
            assert "run_status" in constraints["ck_workflow_event_run_status"]
            assert constraints["uq_workflow_event_sequence"].startswith(
                "UNIQUE (run_id, sequence)"
            )
            assert constraints["uq_workflow_event_key"].startswith(
                "UNIQUE (run_id, event_key)"
            )
            assert constraints["fk_workflow_event_run"].startswith(
                "FOREIGN KEY (run_id) REFERENCES workflow_runs(id)"
            )
            index_definition = await session.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                    "AND tablename = 'workflow_events' "
                    "AND indexname = 'ix_workflow_events_run_sequence'"
                )
            )
            assert index_definition is not None
            assert "(run_id, sequence)" in index_definition
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WorkflowEvent)
                    .where(WorkflowEvent.run_id == legacy_run_id)
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_append_replay_independent_sequences_and_bounded_reads(
    database: Settings,
) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first_run = await _seed_run(sessions)
        second_run = await _seed_run(sessions, status="running")
        async with sessions.begin() as session:
            first = await append_workflow_event(
                session,
                run_id=first_run,
                event_key="run:queued",
                event_type="run.queued",
                run_status="queued",
                metadata={"attempt_count": 1},
            )
            second = await append_workflow_event(
                session,
                run_id=first_run,
                event_key="run:running:start",
                event_type="run.running",
                run_status="running",
                metadata={"attempt_count": 1},
            )
            independent = await append_workflow_event(
                session,
                run_id=second_run,
                event_key="run:running:start",
                event_type="run.running",
                run_status="running",
            )
        assert (first.sequence, second.sequence, independent.sequence) == (1, 2, 1)
        assert first.created_at is not None and first.created_at.utcoffset() is not None
        original_id = first.id
        original_metadata = first.metadata_json

        async with sessions.begin() as session:
            replay = await append_workflow_event(
                session,
                run_id=first_run,
                event_key="run:queued",
                event_type="run.failed",
                run_status="failed",
                metadata={"attempt_count": 99},
            )
            assert replay.id == original_id
            assert replay.event_type == "run.queued"
            assert replay.run_status == "queued"
            assert replay.metadata_json == original_metadata

        async with sessions() as session:
            assert [
                event.sequence
                for event in await read_workflow_events(
                    session, run_id=first_run, after_sequence=0, limit=1
                )
            ] == [1]
            assert [
                event.sequence
                for event in await read_workflow_events(
                    session, run_id=first_run, after_sequence=1, limit=10
                )
            ] == [2]
            with pytest.raises(ValueError, match="after_sequence"):
                await read_workflow_events(session, run_id=first_run, after_sequence=-1)
            for invalid_limit in (0, MAX_EVENT_BATCH_SIZE + 1):
                with pytest.raises(ValueError, match="limit"):
                    await read_workflow_events(
                        session, run_id=first_run, limit=invalid_limit
                    )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "metadata,secret",
    [
        ({"password": "do-not-record"}, "do-not-record"),
        ({"access_token": "do-not-record"}, "do-not-record"),
        ({"reviewReason": "fixed-code-only"}, "reviewReason"),
        (
            {
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ."
                "synthetic_signature": "bounded-value"
            },
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.synthetic_signature",
        ),
        ({"display": "Bearer do-not-record"}, "do-not-record"),
        ({"display": "x" * 1001}, "x" * 1001),
        ({"k" * 1001: "bounded-value"}, "k" * 1001),
        (
            {"a": {"b": {"c": {"d": {"e": {"f": "nested-sentinel"}}}}}},
            "nested-sentinel",
        ),
    ],
    ids=[
        "forbidden-key",
        "token-key",
        "camel-review-reason-key",
        "jwt-shaped-key",
        "bearer-content",
        "long-value",
        "long-key",
        "excessive-depth",
    ],
)
async def test_metadata_rejection_does_not_persist_or_log_values(
    database: Settings,
    caplog: pytest.LogCaptureFixture,
    metadata: dict[str, object],
    secret: str,
) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    try:
        run_id = await _seed_run(sessions)
        with pytest.raises(WorkflowEventMetadataError) as error:
            async with sessions.begin() as session:
                await append_workflow_event(
                    session,
                    run_id=run_id,
                    event_key="invalid-metadata",
                    event_type="run.queued",
                    run_status="queued",
                    metadata=metadata,
                )
        assert secret not in str(error.value)
        assert secret not in caplog.text
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(WorkflowEvent))
                == 0
            )
    finally:
        await engine.dispose()


async def test_missing_run_and_caller_owned_rollback(database: Settings) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            with pytest.raises(WorkflowRunNotFound):
                await append_workflow_event(
                    session,
                    run_id=uuid4(),
                    event_key="missing",
                    event_type="run.queued",
                    run_status="queued",
                )

        run_id = await _seed_run(sessions)
        async with sessions() as session:
            async with session.begin():
                run = await session.get(WorkflowRun, run_id)
                assert run is not None
                run.status = "running"
                event = await append_workflow_event(
                    session,
                    run_id=run_id,
                    event_key="rollback",
                    event_type="run.running",
                    run_status="running",
                )
                assert event.sequence == 1
                await session.flush()
                await session.rollback()

        async with sessions() as session:
            run = await session.get(WorkflowRun, run_id)
            assert run is not None and run.status == "queued"
            assert (
                await session.scalar(select(func.count()).select_from(WorkflowEvent))
                == 0
            )
    finally:
        await engine.dispose()


async def test_concurrent_different_keys_serialize_to_adjacent_sequences(
    database: Settings,
) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        run_id = await _seed_run(sessions)
        ready = 0
        start = asyncio.Event()
        ready_lock = asyncio.Lock()

        async def append(key: str) -> tuple[int, UUID]:
            nonlocal ready
            async with ready_lock:
                ready += 1
                if ready == 2:
                    start.set()
            await start.wait()
            async with sessions.begin() as session:
                event = await append_workflow_event(
                    session,
                    run_id=run_id,
                    event_key=key,
                    event_type="node.completed",
                    node_name=key,
                    run_status="running",
                )
                return event.sequence, event.id

        results = await asyncio.gather(append("first"), append("second"))
        assert sorted(sequence for sequence, _ in results) == [1, 2]
        assert len({event_id for _, event_id in results}) == 2
        async with sessions() as session:
            rows = await read_workflow_events(session, run_id=run_id)
            assert [row.sequence for row in rows] == [1, 2]
            assert {row.event_key for row in rows} == {"first", "second"}
    finally:
        await engine.dispose()
