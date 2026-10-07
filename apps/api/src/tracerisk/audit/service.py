from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.audit.models import AuditEvent


async def append_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    action_key: str,
    actor_id: UUID | None,
    investigation_id: UUID,
    run_id: UUID,
    resource_type: str,
    resource_id: UUID,
    metadata: dict[str, object],
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        action_key=action_key,
        actor_id=actor_id,
        investigation_id=investigation_id,
        run_id=run_id,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata,
    )
    session.add(event)
    await session.flush()
    return event
