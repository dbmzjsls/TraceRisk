import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.auth.models import Role, User
from tracerisk.auth.router import (
    SigningSecret,
    bearer,
    decode_bearer_user_id,
    unauthorized,
)
from tracerisk.investigations.models import WorkflowEvent, WorkflowRun
from tracerisk.workflows.events import _validated_metadata

MAX_SEQUENCE = 2_147_483_647
TERMINAL_STATUSES = {"completed", "failed"}
READABLE_ROLES = {Role.ANALYST.value, Role.REVIEWER.value, Role.ADMIN.value}

router = APIRouter(prefix="/workflow-runs", tags=["workflow-runs"])
DisconnectPredicate = Callable[[], Awaitable[bool]]


class PersistedEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    event_type: str
    node_name: str | None
    run_status: str
    metadata: dict[str, object]
    created_at: datetime

    @field_validator("metadata")
    @classmethod
    def reject_secret_bearing_metadata(
        cls, value: dict[str, object]
    ) -> dict[str, object]:
        return _validated_metadata(value)


class StreamReadyData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    history_drained: bool = True
    history_available: bool
    run_status: str
    high_water_mark: int = Field(ge=0, le=MAX_SEQUENCE)


@dataclass(frozen=True)
class StreamAuthorization:
    run_id: UUID
    user_id: UUID
    role: str


def parse_last_event_id(value: str | None) -> int:
    if value in (None, ""):
        return 0
    if not value.isascii() or not value.isdecimal():
        raise ValueError("Last-Event-ID must be a non-negative decimal integer")
    sequence = int(value)
    if sequence > MAX_SEQUENCE:
        raise ValueError("Last-Event-ID exceeds the accepted sequence range")
    return sequence


def format_persisted_event(event: WorkflowEvent) -> str:
    data = PersistedEventData(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        node_name=event.node_name,
        run_status=event.run_status,
        metadata=event.metadata_json,
        created_at=event.created_at,
    )
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type}\n"
        f"data: {data.model_dump_json()}\n\n"
    )


def format_stream_ready(*, status: str, high_water_mark: int) -> str:
    data = StreamReadyData(
        history_available=high_water_mark > 0,
        run_status=status,
        high_water_mark=high_water_mark,
    )
    return f"event: stream.ready\ndata: {data.model_dump_json()}\n\n"


async def authorize_stream(
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    user_id: UUID,
) -> StreamAuthorization:
    """Authorize inside one short Session and return immutable scalar data."""
    async with sessions() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise unauthorized()
        if user.role not in READABLE_ROLES:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        visible_run_id = await session.scalar(
            select(WorkflowRun.id).where(WorkflowRun.id == run_id)
        )
        if visible_run_id is None:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        return StreamAuthorization(
            run_id=visible_run_id, user_id=user.id, role=user.role
        )


def _snapshot_statement(run_id: UUID) -> Select[tuple[str, int]]:
    return (
        select(
            WorkflowRun.status,
            func.coalesce(func.max(WorkflowEvent.sequence), 0),
        )
        .outerjoin(WorkflowEvent, WorkflowEvent.run_id == WorkflowRun.id)
        .where(WorkflowRun.id == run_id)
        .group_by(WorkflowRun.id, WorkflowRun.status)
    )


async def read_stream_snapshot(
    sessions: async_sessionmaker[AsyncSession], run_id: UUID
) -> tuple[str, int]:
    async with sessions() as session:
        row = (await session.execute(_snapshot_statement(run_id))).one_or_none()
        if row is None:
            raise RuntimeError("Authorized WorkflowRun no longer exists")
        return str(row[0]), int(row[1])


async def _read_bounded_events(
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    after_sequence: int,
    high_water_mark: int,
    batch_size: int,
) -> list[WorkflowEvent]:
    async with sessions() as session:
        rows = await session.scalars(
            select(WorkflowEvent)
            .where(
                WorkflowEvent.run_id == run_id,
                WorkflowEvent.sequence > after_sequence,
                WorkflowEvent.sequence <= high_water_mark,
            )
            .order_by(WorkflowEvent.sequence)
            .limit(batch_size)
        )
        return list(rows)


async def _wait_for_poll(seconds: float, is_disconnected: DisconnectPredicate) -> bool:
    deadline = monotonic() + seconds
    while True:
        if await is_disconnected():
            return False
        remaining = deadline - monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(remaining, 0.05))


async def stream_workflow_events(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
    starting_sequence: int,
    is_disconnected: DisconnectPredicate,
    *,
    poll_interval: float,
    heartbeat_interval: float,
    batch_size: int,
) -> AsyncIterator[str]:
    if starting_sequence < 0 or starting_sequence > MAX_SEQUENCE:
        raise ValueError("starting_sequence is outside the accepted range")
    if poll_interval <= 0 or heartbeat_interval <= 0:
        raise ValueError("stream intervals must be positive")
    if batch_size < 1 or batch_size > 500:
        raise ValueError("batch_size must be between 1 and 500")

    cursor = starting_sequence
    ready_sent = False
    last_frame_at = monotonic()
    yield "retry: 2000\n\n"
    last_frame_at = monotonic()

    while True:
        if await is_disconnected():
            return
        status, high_water_mark = await read_stream_snapshot(sessions, run_id)
        while cursor < high_water_mark:
            if await is_disconnected():
                return
            batch = await _read_bounded_events(
                sessions,
                run_id=run_id,
                after_sequence=cursor,
                high_water_mark=high_water_mark,
                batch_size=batch_size,
            )
            if not batch:
                raise RuntimeError("Workflow event sequence is incomplete")
            for event in batch:
                if event.sequence != cursor + 1:
                    raise RuntimeError("Workflow event sequence is incomplete")
                yield format_persisted_event(event)
                cursor = event.sequence
                last_frame_at = monotonic()

        if not ready_sent:
            yield format_stream_ready(status=status, high_water_mark=high_water_mark)
            ready_sent = True
            last_frame_at = monotonic()

        if status in TERMINAL_STATUSES:
            return
        if monotonic() - last_frame_at >= heartbeat_interval:
            yield f": heartbeat {int(monotonic() * 1000)}\n\n"
            last_frame_at = monotonic()
        if not await _wait_for_poll(poll_interval, is_disconnected):
            return


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def workflow_run_events(
    request: Request,
    run_id: UUID,
    secret: SigningSecret,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        starting_sequence = parse_last_event_id(last_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    user_id = decode_bearer_user_id(credentials, secret)
    sessions: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    authorization = await authorize_stream(sessions, run_id=run_id, user_id=user_id)
    body = stream_workflow_events(
        sessions,
        authorization.run_id,
        starting_sequence,
        request.is_disconnected,
        poll_interval=1.0,
        heartbeat_interval=15.0,
        batch_size=100,
    )
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
