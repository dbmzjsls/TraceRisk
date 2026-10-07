"""Combined service failure, shutdown, readiness and lifecycle guards."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from scripts import database_lifecycle as lifecycle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.auth.seed import seed_accounts
from tracerisk.config import Settings
from tracerisk.investigations.models import DispatchOutbox, WorkflowRun, WorkflowStep
from tracerisk.main import app as api
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog
from tracerisk.workflows import service, spike_graph
from tracerisk.workflows.health import HealthReporter, is_healthy
from tracerisk.workflows.queue import create_queue
from tracerisk.workflows.service import supervise
from tracerisk.workflows.spike_graph import initialize_checkpoints


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, object]]:
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.mark.anyio
async def test_supervisor_propagates_worker_failure_and_cancels_dispatcher() -> None:
    cancelled = asyncio.Event()

    async def dispatcher() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def worker() -> None:
        raise RuntimeError("worker failed")

    with pytest.raises(RuntimeError, match="worker failed"):
        await supervise(dispatcher(), worker(), asyncio.Event().wait(), asyncio.Event())
    assert cancelled.is_set()


@pytest.mark.anyio
async def test_supervisor_shutdown_closes_both_responsibilities() -> None:
    stopped: set[str] = set()
    stop = asyncio.Event()

    async def responsibility(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(name)

    task = asyncio.create_task(
        supervise(
            responsibility("dispatcher"),
            responsibility("worker"),
            responsibility("heartbeat"),
            stop,
        )
    )
    await asyncio.sleep(0)
    stop.set()
    await task
    assert stopped == {"dispatcher", "worker", "heartbeat"}


@pytest.mark.anyio
async def test_supervisor_cancels_unfinished_real_worker_job(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    monkeypatch.setattr(service, "WORKER_SHUTDOWN_GRACE_SECONDS", 0.1)
    queue = create_queue(database)

    @queue.task(name="test.service_unfinished_job", queue="tracerisk")
    async def unfinished_job() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def idle() -> None:
        await asyncio.Event().wait()

    async with queue.open_async() as app:
        await app.schema_manager.apply_schema_async()
        await app.configure_task("test.service_unfinished_job").defer_async()
        stop = asyncio.Event()
        supervisor = asyncio.create_task(
            supervise(idle(), service.consume(app), idle(), stop)
        )
        try:
            async with asyncio.timeout(10):
                await started.wait()
            stop.set()
            async with asyncio.timeout(5):
                await supervisor
            assert cancelled.is_set()
        finally:
            if not supervisor.done():
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)


def test_health_requires_fresh_dispatch_and_queue_evidence(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    reporter = HealthReporter(path)
    try:
        assert not is_healthy(path)
        reporter.mark_dispatcher()
        assert not is_healthy(path)
        reporter.mark_worker()
        assert is_healthy(path)
        state = json.loads(path.read_text(encoding="utf-8"))
        state["dispatcher_scan"] = time.time() - 60
        path.write_text(json.dumps(state), encoding="utf-8")
        assert not is_healthy(path)
    finally:
        reporter.close()
    assert not path.exists()


def test_lifecycle_rejects_retained_target() -> None:
    with pytest.raises(ValueError, match="outside the demo/generated targets"):
        lifecycle._require_target("tracerisk")
    with pytest.raises(ValueError, match="outside the demo/generated targets"):
        lifecycle._require_target("postgres")


def test_initialization_confirms_identity_before_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        postgres_db="tracerisk_demo",
        model_adapter="mock",
        embedding_provider="fake",
        embedding_model="fake-sha256-v1",
    )
    monkeypatch.setattr(lifecycle, "current_database", lambda *_: "tracerisk")
    with pytest.raises(RuntimeError, match="differs from lifecycle target"):
        lifecycle.initialize_target(settings, "tracerisk_demo")


def test_partial_bootstrap_retry_uses_exact_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        postgres_db="tracerisk_demo",
        model_adapter="mock",
        embedding_provider="fake",
        embedding_model="fake-sha256-v1",
    )
    events: list[str] = []
    monkeypatch.setattr(lifecycle, "current_database", lambda _, target: target)
    monkeypatch.setattr(lifecycle, "_reject_non_fake_index", lambda *_: None)
    monkeypatch.setattr(command, "upgrade", lambda *_: events.append("alembic"))

    async def initialize(_: Settings) -> None:
        events.append("frameworks-and-seed")

    monkeypatch.setattr(lifecycle, "_initialize_async", initialize)
    monkeypatch.setattr(
        lifecycle,
        "verify_target",
        lambda *_: events.append("verify"),
    )
    previous = os.environ.get("POSTGRES_DB")
    try:
        lifecycle.initialize_target(settings, "tracerisk_demo")
        lifecycle.initialize_target(settings, "tracerisk_demo")
    finally:
        assert os.environ.get("POSTGRES_DB") == previous
    assert events == ["alembic", "frameworks-and-seed", "verify"] * 2


def test_owned_lifecycle_initializes_real_queue_schema_twice() -> None:
    settings = Settings(
        _env_file=None,
        model_adapter="mock",
        embedding_provider="fake",
        embedding_model="fake-sha256-v1",
    )
    owned = lifecycle.create_owned_database(settings, purpose="e2e", run_id=uuid4().hex)
    try:
        lifecycle.initialize_target(settings, owned.name)
        lifecycle.initialize_target(settings, owned.name)
        assert lifecycle.verify_target(settings, owned.name)["database"] == owned.name
    finally:
        lifecycle.drop_owned_database(settings, owned)


def test_owned_lifecycle_retries_after_post_queue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        model_adapter="mock",
        embedding_provider="fake",
        embedding_model="fake-sha256-v1",
    )
    owned = lifecycle.create_owned_database(settings, purpose="e2e", run_id=uuid4().hex)
    original_initialize_checkpoints = spike_graph.initialize_checkpoints
    calls = 0

    async def fail_once_after_queue() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("checkpoint setup failed")
        await original_initialize_checkpoints()

    monkeypatch.setattr(spike_graph, "initialize_checkpoints", fail_once_after_queue)
    try:
        with pytest.raises(RuntimeError, match="checkpoint setup failed"):
            lifecycle.initialize_target(settings, owned.name)
        lifecycle.initialize_target(settings, owned.name)
        assert calls == 2
        assert lifecycle.verify_target(settings, owned.name)["database"] == owned.name
    finally:
        lifecycle.drop_owned_database(settings, owned)


def test_preexisting_demo_with_other_index_stops_before_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        postgres_db="tracerisk_demo",
        model_adapter="mock",
        embedding_provider="fake",
        embedding_model="fake-sha256-v1",
    )
    monkeypatch.setattr(lifecycle, "current_database", lambda _, target: target)

    def reject(*_: object) -> None:
        raise RuntimeError("Existing target has a non-Fake active index")

    monkeypatch.setattr(lifecycle, "_reject_non_fake_index", reject)
    monkeypatch.setattr(
        command,
        "upgrade",
        lambda *_: pytest.fail("Migration must not run on a non-Fake target"),
    )
    with pytest.raises(RuntimeError, match="non-Fake active index"):
        lifecycle.initialize_target(settings, "tracerisk_demo")


def test_owned_record_rejects_non_generated_cleanup(tmp_path: Path) -> None:
    record = tmp_path / "owned.json"
    record.write_text(
        json.dumps({"name": "tracerisk", "purpose": "e2e", "run_id": "a" * 32}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match"):
        lifecycle._read_record(record)


@pytest.mark.anyio
async def test_public_investigation_advances_without_manual_dispatch(
    database: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "JWT_SECRET", "test-only-workflow-service-signing-secret-long-enough"
    )
    monkeypatch.setattr(
        service,
        "HealthReporter",
        lambda: HealthReporter(tmp_path / "service-health.json"),
    )
    await initialize_checkpoints()
    engine = create_async_engine(database.database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with create_queue(database).open_async() as queue:
            await queue.schema_manager.apply_schema_async()
        catalog = generate_catalog()
        async with sessions() as session:
            await seed_accounts(session)
        async with sessions.begin() as session:
            await import_catalog(session, catalog)
            await build_index(session, FakeEmbedding(), IndexContract.fake())
        async with api.router.lifespan_context(api):
            async with AsyncClient(
                transport=ASGITransport(app=api), base_url="http://test"
            ) as client:
                login = await client.post(
                    "/auth/login",
                    json={"username": "analyst", "password": "AnalystDemo!2026"},
                )
                assert login.status_code == 200
                headers = {
                    "Authorization": f"Bearer {login.json()['access_token']}",
                    "Idempotency-Key": "combined-service-public-investigation",
                }
                response = await client.post(
                    "/investigations",
                    headers=headers,
                    json={
                        "company_id": str(catalog[0].company.id),
                        "as_of_date": "2026-09-11",
                    },
                )
                assert response.status_code == 200
                run_id = response.json()["current_run"]["id"]
                task = asyncio.create_task(service.serve())
                try:
                    async with asyncio.timeout(40):
                        while True:
                            async with sessions() as session:
                                status = await session.scalar(
                                    select(WorkflowRun.status).where(
                                        WorkflowRun.id == run_id
                                    )
                                )
                            if status in {"awaiting_review", "completed"}:
                                break
                            if task.done():
                                task.result()
                            await asyncio.sleep(0.2)
                    assert status in {"awaiting_review", "completed"}
                    async with sessions() as session:
                        outbox = await session.scalar(
                            select(DispatchOutbox).where(
                                DispatchOutbox.run_id == run_id
                            )
                        )
                        probe = await session.scalar(
                            select(WorkflowStep).where(
                                WorkflowStep.run_id == run_id,
                                WorkflowStep.name == "dispatch_probe",
                            )
                        )
                        assert outbox is not None and outbox.dispatched_at is not None
                        assert probe is not None
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    finally:
        await engine.dispose()
