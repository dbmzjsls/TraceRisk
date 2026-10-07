import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.investigations.models import WorkflowEvent, WorkflowRun

MAX_EVENT_BATCH_SIZE = 500
MAX_METADATA_DEPTH = 5
MAX_METADATA_ITEMS = 100
MAX_METADATA_TEXT_LENGTH = 1000
MAX_METADATA_TOTAL_TEXT_LENGTH = 4000

_EVENT_TYPES = {
    "run.queued",
    "run.running",
    "node.completed",
    "run.awaiting_review",
    "run.resume_queued",
    "run.completed",
    "run.failed",
}
_RUN_STATUSES = {
    "queued",
    "running",
    "awaiting_review",
    "resume_queued",
    "completed",
    "failed",
}
_FORBIDDEN_KEYS = {
    "api_key",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "evidence",
    "idempotency_key",
    "jwt",
    "password",
    "passwd",
    "prompt",
    "pwd",
    "reason",
    "reason_text",
    "response",
    "review_reason",
    "secret",
    "token",
}
_FORBIDDEN_KEY_FRAGMENTS = {
    "apikey",
    "authorization",
    "credential",
    "evidence",
    "idempotencykey",
    "jwt",
    "passwd",
    "password",
    "prompt",
    "response",
    "secret",
    "token",
}
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_CONTENT_PATTERNS = (
    re.compile(r"\bbearer\s+\S+", re.IGNORECASE),
    _JWT_PATTERN,
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"\b(?:api[_-]?key|authorization|password|secret|token)\s*[:=]",
        re.IGNORECASE,
    ),
)


class WorkflowRunNotFound(ValueError):
    """The event's WorkflowRun does not exist."""


class WorkflowEventMetadataError(ValueError):
    """Workflow event metadata is unsafe or exceeds its fixed bounds."""


def _normalized_key(key: str) -> str:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    return re.sub(r"[^a-z0-9]+", "_", snake_case.lower()).strip("_")


def _validated_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise WorkflowEventMetadataError("Workflow event metadata must be an object")

    items = 0
    total_text = 0

    def visit(value: object, depth: int) -> None:
        nonlocal items, total_text
        if depth > MAX_METADATA_DEPTH:
            raise WorkflowEventMetadataError(
                "Workflow event metadata is too deeply nested"
            )
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise WorkflowEventMetadataError(
                        "Workflow event metadata keys must be strings"
                    )
                items += 1
                total_text += len(key)
                if len(key) > MAX_METADATA_TEXT_LENGTH:
                    raise WorkflowEventMetadataError(
                        "Workflow event metadata text exceeds the per-value limit"
                    )
                if total_text > MAX_METADATA_TOTAL_TEXT_LENGTH:
                    raise WorkflowEventMetadataError(
                        "Workflow event metadata exceeds the total text limit"
                    )
                normalized = _normalized_key(key)
                compact = normalized.replace("_", "")
                if (
                    not normalized
                    or normalized in _FORBIDDEN_KEYS
                    or any(fragment in compact for fragment in _FORBIDDEN_KEY_FRAGMENTS)
                    or any(pattern.search(key) for pattern in _SECRET_CONTENT_PATTERNS)
                ):
                    raise WorkflowEventMetadataError(
                        "Workflow event metadata contains a forbidden key"
                    )
                visit(nested, depth + 1)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                items += 1
                visit(nested, depth + 1)
        elif isinstance(value, str):
            items += 1
            total_text += len(value)
            if len(value) > MAX_METADATA_TEXT_LENGTH:
                raise WorkflowEventMetadataError(
                    "Workflow event metadata text exceeds the per-value limit"
                )
            if any(pattern.search(value) for pattern in _SECRET_CONTENT_PATTERNS):
                raise WorkflowEventMetadataError(
                    "Workflow event metadata contains secret-bearing content"
                )
        elif value is None or isinstance(value, (bool, int)):
            items += 1
        elif isinstance(value, float):
            items += 1
            if not math.isfinite(value):
                raise WorkflowEventMetadataError(
                    "Workflow event metadata numbers must be finite"
                )
        else:
            raise WorkflowEventMetadataError(
                "Workflow event metadata contains a non-JSON value"
            )
        if items > MAX_METADATA_ITEMS:
            raise WorkflowEventMetadataError(
                "Workflow event metadata has too many items"
            )
        if total_text > MAX_METADATA_TOTAL_TEXT_LENGTH:
            raise WorkflowEventMetadataError(
                "Workflow event metadata exceeds the total text limit"
            )

    visit(metadata, 0)
    return deepcopy(dict(metadata))


async def append_workflow_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    event_key: str,
    event_type: str,
    run_status: str,
    node_name: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> WorkflowEvent:
    """Append or replay one event; the caller owns the surrounding transaction."""
    if not event_key.strip():
        raise ValueError("event_key must not be empty")
    if event_type not in _EVENT_TYPES:
        raise ValueError("event_type is not supported")
    if run_status not in _RUN_STATUSES:
        raise ValueError("run_status is not supported")
    if node_name is not None and not node_name.strip():
        raise ValueError("node_name must be null or non-empty")
    metadata_json = _validated_metadata(metadata)

    run = await session.get(WorkflowRun, run_id, with_for_update=True)
    if run is None:
        raise WorkflowRunNotFound("WorkflowRun does not exist")

    existing = await session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.run_id == run_id,
            WorkflowEvent.event_key == event_key,
        )
    )
    if existing is not None:
        return existing

    last_sequence = await session.scalar(
        select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run_id)
    )
    event = WorkflowEvent(
        run_id=run_id,
        sequence=(last_sequence or 0) + 1,
        event_key=event_key,
        event_type=event_type,
        node_name=node_name,
        run_status=run_status,
        metadata_json=metadata_json,
    )
    session.add(event)
    await session.flush()
    return event


async def read_workflow_events(
    session: AsyncSession,
    *,
    run_id: UUID,
    after_sequence: int = 0,
    limit: int = 100,
) -> Sequence[WorkflowEvent]:
    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    if limit < 1 or limit > MAX_EVENT_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_EVENT_BATCH_SIZE}")
    events = await session.scalars(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.run_id == run_id,
            WorkflowEvent.sequence > after_sequence,
        )
        .order_by(WorkflowEvent.sequence)
        .limit(limit)
    )
    return list(events)
