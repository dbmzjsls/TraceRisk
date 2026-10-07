import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from tracerisk.auth.models import User
from tracerisk.auth.seed import seed_accounts
from tracerisk.auth.service import create_access_token
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.investigations.models import Investigation, WorkflowEvent, WorkflowRun
from tracerisk.main import app
from tracerisk.workflows.events import append_workflow_event
from tracerisk.workflows.sse import (
    format_persisted_event,
    parse_last_event_id,
    stream_workflow_events,
)

SECRET = "sse-test-signing-secret-at-least-48-characters"
pytestmark = pytest.mark.anyio


async def test_openapi_declares_sse_success_media_type() -> None:
    response = app.openapi()["paths"]["/workflow-runs/{run_id}/events"]["get"][
        "responses"
    ]["200"]

    assert response["content"] == {"text/event-stream": {"schema": {"type": "string"}}}


@pytest.fixture
async def client(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            await seed_accounts(session)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as connection:
            yield connection


async def identity(username: str) -> tuple[UUID, dict[str, str]]:
    async with app.state.session_factory() as session:
        user = await session.scalar(select(User).where(User.username == username))
        assert user is not None
        return user.id, {
            "Authorization": f"Bearer {create_access_token(user.id, SECRET)}"
        }


async def seed_run(*, status: str = "queued") -> UUID:
    owner_id, _ = await identity("analyst")
    company_id, investigation_id, run_id = uuid4(), uuid4(), uuid4()
    async with app.state.session_factory.begin() as session:
        session.add_all(
            [
                Company(
                    id=company_id,
                    synthetic_key=f"sse-{company_id}",
                    name="SSE 测试企业",
                    unified_credit_code=str(company_id),
                ),
                Investigation(
                    id=investigation_id,
                    company_id=company_id,
                    created_by=owner_id,
                    as_of_date=date(2026, 9, 21),
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


async def append(
    run_id: UUID,
    sequence_name: str,
    event_type: str,
    status: str,
    *,
    node_name: str | None = None,
) -> None:
    async with app.state.session_factory.begin() as session:
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key=sequence_name,
            event_type=event_type,
            run_status=status,
            node_name=node_name,
            metadata={"attempt_count": 1, "label": "中文"},
        )


def parsed_frame(frame: str) -> tuple[str | None, str | None, dict[str, object]]:
    fields: dict[str, str] = {}
    for line in frame.rstrip("\n").splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields.get("id"), fields.get("event"), json.loads(fields["data"])


def checked_out(engine: AsyncEngine) -> int:
    return cast(QueuePool, engine.sync_engine.pool).checkedout()


async def next_frame(stream: AsyncIterator[str]) -> str:
    return await anext(stream)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), ("", 0), ("0", 0), ("2147483647", 2147483647)],
)
def test_last_event_id_accepts_only_sequence_range(
    value: str | None, expected: int
) -> None:
    assert parse_last_event_id(value) == expected


@pytest.mark.parametrize("value", ["-1", "+1", "1.0", " 1", "1 ", "x", "2147483648"])
def test_last_event_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_last_event_id(value)


def test_persisted_frame_revalidates_metadata_and_rejects_secrets() -> None:
    event = WorkflowEvent(
        id=uuid4(),
        run_id=uuid4(),
        sequence=1,
        event_key="unsafe",
        event_type="run.running",
        node_name=None,
        run_status="running",
        metadata_json={"authorization": "redacted"},
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError):
        format_persisted_event(event)


async def test_terminal_history_format_ready_and_reconnect(
    client: httpx.AsyncClient,
) -> None:
    run_id = await seed_run(status="running")
    await append(run_id, "one", "run.running", "running")
    await append(run_id, "two", "node.completed", "running", node_name="节点")
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "completed"
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="three",
            event_type="run.completed",
            run_status="completed",
        )

    _, headers = await identity("analyst")
    response = await client.get(f"/workflow-runs/{run_id}/events", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    frames = [part + "\n\n" for part in response.text.split("\n\n") if part]
    assert frames[0] == "retry: 2000\n\n"
    persisted = [parsed_frame(frame) for frame in frames[1:4]]
    assert [item[0] for item in persisted] == ["1", "2", "3"]
    assert [item[1] for item in persisted] == [
        "run.running",
        "node.completed",
        "run.completed",
    ]
    assert persisted[1][2]["node_name"] == "节点"
    assert "中文" in frames[2]
    assert frames[4].startswith("event: stream.ready\n")
    ready = parsed_frame(frames[4])
    assert ready[0] is None
    assert ready[2] == {
        "history_drained": True,
        "history_available": True,
        "run_status": "completed",
        "high_water_mark": 3,
    }

    resumed = await client.get(
        f"/workflow-runs/{run_id}/events",
        headers={**headers, "Last-Event-ID": "2"},
    )
    assert "id: 1" not in resumed.text
    assert "id: 2" not in resumed.text
    assert resumed.text.count("id: 3") == 1
    assert resumed.text.count("event: stream.ready") == 1


async def test_authorization_visibility_validation_and_release(
    client: httpx.AsyncClient,
) -> None:
    run_id = await seed_run(status="completed")
    await append(run_id, "done", "run.completed", "completed")
    engine: AsyncEngine = app.state.session_factory.kw["bind"]

    assert (await client.get(f"/workflow-runs/{run_id}/events")).status_code == 401
    assert checked_out(engine) == 0
    _, analyst_headers = await identity("analyst")
    missing = await client.get(
        f"/workflow-runs/{uuid4()}/events", headers=analyst_headers
    )
    assert missing.status_code == 404
    assert checked_out(engine) == 0
    invalid = await client.get(
        f"/workflow-runs/{run_id}/events",
        headers={**analyst_headers, "Last-Event-ID": "-1"},
    )
    assert invalid.status_code == 422
    malformed = await client.get(
        f"/workflow-runs/{run_id}/events",
        headers={"Authorization": "Bearer malformed"},
    )
    assert malformed.status_code == 401
    for role in ("analyst", "reviewer", "admin"):
        _, headers = await identity(role)
        assert (
            await client.get(f"/workflow-runs/{run_id}/events", headers=headers)
        ).status_code == 200
        assert checked_out(engine) == 0


async def test_endpoint_idle_stream_releases_pool_and_disconnects(
    client: httpx.AsyncClient,
) -> None:
    run_id = await seed_run(status="awaiting_review")
    _, auth_headers = await identity("analyst")
    token = auth_headers["Authorization"].encode()
    ready = asyncio.Event()
    disconnect = asyncio.Event()
    first_receive = True

    async def receive() -> dict[str, object]:
        nonlocal first_receive
        if first_receive:
            first_receive = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        body = message.get("body", b"")
        assert isinstance(body, bytes)
        if message["type"] == "http.response.body" and b"stream.ready" in body:
            ready.set()

    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/workflow-runs/{run_id}/events",
        "raw_path": f"/workflow-runs/{run_id}/events".encode(),
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"authorization", token)],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "root_path": "",
    }
    task = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
    await asyncio.wait_for(ready.wait(), timeout=0.5)
    engine: AsyncEngine = app.state.session_factory.kw["bind"]
    assert checked_out(engine) == 0
    disconnect.set()
    await asyncio.wait_for(task, timeout=0.5)
    assert checked_out(engine) == 0


async def test_generator_terminal_multibatch_and_eventless_legacy(
    client: httpx.AsyncClient,
) -> None:
    run_id = await seed_run(status="running")
    for index in range(5):
        await append(
            run_id, f"node-{index}", "node.completed", "running", node_name=f"n{index}"
        )
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "failed"
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="failed",
            event_type="run.failed",
            run_status="failed",
        )
    frames = [
        frame
        async for frame in stream_workflow_events(
            app.state.session_factory,
            run_id,
            0,
            lambda: _false(),
            poll_interval=0.01,
            heartbeat_interval=1,
            batch_size=2,
        )
    ]
    assert [parsed_frame(frame)[0] for frame in frames if frame.startswith("id:")] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    ]
    assert frames[-1].startswith("event: stream.ready")

    legacy_id = await seed_run(status="completed")
    legacy = [
        frame
        async for frame in stream_workflow_events(
            app.state.session_factory,
            legacy_id,
            0,
            lambda: _false(),
            poll_interval=0.01,
            heartbeat_interval=1,
            batch_size=2,
        )
    ]
    assert parsed_frame(legacy[-1])[2]["history_available"] is False


async def _false() -> bool:
    return False


async def test_generator_waiting_live_heartbeat_disconnect_and_pool_release(
    client: httpx.AsyncClient,
) -> None:
    run_id = await seed_run(status="awaiting_review")
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    stream = stream_workflow_events(
        app.state.session_factory,
        run_id,
        0,
        is_disconnected,
        poll_interval=0.01,
        heartbeat_interval=0.02,
        batch_size=2,
    )
    assert await anext(stream) == "retry: 2000\n\n"
    ready = await anext(stream)
    assert parsed_frame(ready)[1] == "stream.ready"
    engine: AsyncEngine = app.state.session_factory.kw["bind"]
    assert checked_out(engine) == 0
    heartbeat = await asyncio.wait_for(anext(stream), timeout=0.2)
    assert heartbeat.startswith(": heartbeat ")
    assert checked_out(engine) == 0
    await append(run_id, "resume", "run.resume_queued", "resume_queued")
    live = await asyncio.wait_for(anext(stream), timeout=0.2)
    assert parsed_frame(live)[:2] == ("1", "run.resume_queued")
    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=0.2)
    assert checked_out(engine) == 0


async def test_terminal_transition_between_polls_is_drained(
    client: httpx.AsyncClient,
) -> None:
    run_id = await seed_run(status="running")
    stream = stream_workflow_events(
        app.state.session_factory,
        run_id,
        0,
        lambda: _false(),
        poll_interval=0.03,
        heartbeat_interval=1,
        batch_size=1,
    )
    assert await anext(stream) == "retry: 2000\n\n"
    assert parsed_frame(await anext(stream))[1] == "stream.ready"
    pending = asyncio.create_task(next_frame(stream))
    await asyncio.sleep(0.005)
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "completed"
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="terminal",
            event_type="run.completed",
            run_status="completed",
        )
    terminal = await asyncio.wait_for(pending, timeout=0.3)
    assert parsed_frame(terminal)[:2] == ("1", "run.completed")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=0.2)
