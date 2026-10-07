import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import procrastinate
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.config import Settings
from tracerisk.investigations.models import (
    DispatchOutbox,
    Investigation,
    WorkflowRun,
    WorkflowStep,
)
from tracerisk.investigations.service import create_investigation
from tracerisk.workflows import outbox, worker
from tracerisk.workflows.queue import QUEUE_NAME, enqueue_run

pytestmark = pytest.mark.anyio
Resources = tuple[async_sessionmaker[AsyncSession], procrastinate.App]


@pytest.fixture
async def resources(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Resources]:
    # Isolate Task 1.2's dispatch probe; Task 1.3 tests exercise the real graph.
    async def no_graph(*args: object) -> None:
        return None

    monkeypatch.setattr(worker, "execute_workflow", no_graph)
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with worker.create_worker(database, sessions).open_async() as app:
            await app.schema_manager.apply_schema_async()
            yield sessions, app
    finally:
        await engine.dispose()


async def create_run(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    async with sessions.begin() as session:
        investigation = await create_investigation(
            session,
            company_id=uuid4(),
            created_by=uuid4(),
            as_of_date=date(2026, 9, 10),
        )
        return investigation.current_run_id


async def consume(app: procrastinate.App) -> None:
    await app.run_worker_async(
        queues=[QUEUE_NAME], wait=False, install_signal_handlers=False, concurrency=2
    )


async def test_creation_rolls_back_all_records(resources: Resources) -> None:
    sessions, app = resources
    with pytest.raises(RuntimeError, match="before commit"):
        async with sessions.begin() as session:
            await create_investigation(
                session,
                company_id=uuid4(),
                created_by=uuid4(),
                as_of_date=date(2026, 9, 10),
            )
            raise RuntimeError("before commit")
    async with sessions() as session:
        for model in (Investigation, WorkflowRun, DispatchOutbox):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert await outbox.dispatch_once(sessions, app) == 0


async def test_process_exit_after_commit_is_compensated(resources: Resources) -> None:
    sessions, app = resources
    script = """
import asyncio, os
from datetime import date
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from tracerisk.config import Settings
from tracerisk.investigations.service import create_investigation
async def main():
    engine = create_async_engine(Settings().database_url)
    async with async_sessionmaker(engine).begin() as session:
        await create_investigation(session, company_id=uuid4(), created_by=uuid4(),
                                   as_of_date=date(2026, 9, 10))
    os._exit(17)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        timeout=20,
    )
    assert result.returncode == 17
    assert await outbox.dispatch_once(sessions, app) == 1
    assert await outbox.dispatch_once(sessions, app) == 0
    await consume(app)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == 1
        assert await session.scalar(select(WorkflowRun.status)) == "running"
        assert (
            await session.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 1
        )


@pytest.mark.parametrize("consume_before_rescan", [False, True])
async def test_enqueue_acknowledgement_gap(
    resources: Resources, monkeypatch: pytest.MonkeyPatch, consume_before_rescan: bool
) -> None:
    sessions, app = resources
    await create_run(sessions)

    async def crash_after_enqueue(
        queue: procrastinate.App, run_id: UUID, operation: str
    ) -> None:
        await enqueue_run(queue, run_id, operation)
        raise RuntimeError("after enqueue")

    with monkeypatch.context() as patch:
        patch.setattr(outbox, "enqueue_run", crash_after_enqueue)
        with pytest.raises(RuntimeError, match="after enqueue"):
            await outbox.dispatch_once(sessions, app)
    async with sessions() as session:
        assert await session.scalar(select(DispatchOutbox.dispatched_at)) is None
    if consume_before_rescan:
        await consume(app)
    assert await outbox.dispatch_once(sessions, app) == 1
    await consume(app)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == 1
        assert await session.scalar(select(DispatchOutbox.dispatched_at)) is not None
        if not consume_before_rescan:
            assert (
                await session.scalar(text("SELECT count(*) FROM procrastinate_jobs"))
                == 1
            )


async def test_concurrent_dispatch_and_execution(resources: Resources) -> None:
    sessions, app = resources
    run_id = await create_run(sessions)
    counts = await asyncio.gather(
        outbox.dispatch_once(sessions, app), outbox.dispatch_once(sessions, app)
    )
    assert sum(counts) == 1
    await asyncio.gather(
        worker.execute_probe(sessions, run_id), worker.execute_probe(sessions, run_id)
    )
    await consume(app)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == 1


@pytest.mark.parametrize("commit_result", [False, True])
async def test_worker_process_death_and_recovery(
    resources: Resources, commit_result: bool
) -> None:
    sessions, app = resources
    run_id = await create_run(sessions)
    await outbox.dispatch_once(sessions, app)
    script = f"""
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import update
from tracerisk.config import Settings
from tracerisk.investigations.models import WorkflowRun
from tracerisk.workflows import worker
original = worker.execute_probe
async def crash(sessions, run_id, attempt_count=1):
    if {commit_result!r}:
        await original(sessions, run_id, attempt_count)
    else:
        async with sessions.begin() as session:
            await session.execute(update(WorkflowRun).where(WorkflowRun.id == run_id)
                                  .values(status='running'))
            os._exit(19)
    os._exit(19)
worker.execute_probe = crash
async def main():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    queue = worker.create_worker(settings, async_sessionmaker(engine))
    async with queue.open_async() as app:
        await app.run_worker_async(queues=['tracerisk'], wait=False,
                                  install_signal_handlers=False)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        timeout=20,
    )
    assert result.returncode == 19
    async with sessions.begin() as session:
        assert (
            await session.scalar(text("SELECT status FROM procrastinate_jobs"))
            == "doing"
        )
        # Simulate expiry only in this isolated database; no 30-second test sleep.
        await session.execute(
            text(
                "UPDATE procrastinate_workers "
                "SET last_heartbeat = now() - interval '1 minute'"
            )
        )
    # An unacknowledged outbox must reuse a doing job, not block its recovery.
    await enqueue_run(app, run_id, "start")
    assert await outbox.recover_stalled(app) == 1
    assert await outbox.recover_stalled(app) == 0
    await consume(app)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == 1
        assert (
            await session.scalar(text("SELECT status FROM procrastinate_jobs"))
            == "succeeded"
        )


@pytest.mark.parametrize(
    "state", ["failed", "completed", "awaiting_review", "resume_queued", "noncurrent"]
)
async def test_terminal_and_noncurrent_runs_are_ignored(
    resources: Resources, state: str
) -> None:
    sessions, app = resources
    old_id = await create_run(sessions)
    async with sessions.begin() as session:
        investigation = await session.scalar(select(Investigation))
        assert investigation is not None
        if state == "noncurrent":
            new_id = uuid4()
            session.add(
                WorkflowRun(id=new_id, investigation_id=investigation.id, run_number=2)
            )
            investigation.current_run_id = new_id
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == old_id)
            .values(status="running" if state == "noncurrent" else state)
        )
    await outbox.dispatch_once(sessions, app)
    await consume(app)
    await worker.execute_probe(sessions, old_id)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == 0


async def test_outbox_uniqueness_and_current_run_ownership(
    resources: Resources,
) -> None:
    sessions, _ = resources
    first = await create_run(sessions)
    second = await create_run(sessions)
    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            session.add(DispatchOutbox(run_id=first, operation="start"))
    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            await session.execute(
                update(Investigation)
                .where(Investigation.current_run_id == first)
                .values(current_run_id=second)
            )


@pytest.mark.parametrize("persistent", [False, True])
async def test_retry_success_or_exhaustion(
    resources: Resources, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    sessions, app = resources
    await create_run(sessions)
    await outbox.dispatch_once(sessions, app)

    original = worker.execute_probe
    attempts = 0

    async def fail(
        factory: async_sessionmaker[AsyncSession], run_id: UUID, attempt_count: int
    ) -> None:
        nonlocal attempts
        attempts += 1
        if not persistent and attempts == 3:
            await original(factory, run_id, attempt_count)
            return
        raise RuntimeError("persistent probe failure")

    monkeypatch.setattr(worker, "execute_probe", fail)
    for _ in range(3):
        await consume(app)
        await asyncio.sleep(1.05)
    async with sessions() as session:
        assert attempts == 3
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
        assert await session.scalar(select(WorkflowRun.status)) == (
            "failed" if persistent else "running"
        )
        assert await session.scalar(text("SELECT status FROM procrastinate_jobs")) == (
            "failed" if persistent else "succeeded"
        )
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == (
            0 if persistent else 1
        )
        if not persistent:
            assert await session.scalar(select(WorkflowStep.attempt_count)) == 3


async def test_queue_lock_limits_waiting_jobs(resources: Resources) -> None:
    sessions, app = resources
    run_id = await create_run(sessions)
    await asyncio.gather(
        enqueue_run(app, run_id, "start"), enqueue_run(app, run_id, "start")
    )
    async with sessions() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 1
        )


async def test_enqueue_failure_leaves_pending(
    resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, app = resources
    await create_run(sessions)

    async def unavailable(*args: object) -> None:
        raise ConnectionError("queue unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(outbox, "enqueue_run", unavailable)
        with pytest.raises(ConnectionError):
            await outbox.dispatch_once(sessions, app)
    async with sessions() as session:
        assert await session.scalar(select(DispatchOutbox.dispatched_at)) is None
        assert (
            await session.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 0
        )
    assert await outbox.dispatch_once(sessions, app) == 1


def test_migration_roundtrip(database: Settings) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.downgrade(config, "0000")
    command.upgrade(config, "head")
    command.check(config)
