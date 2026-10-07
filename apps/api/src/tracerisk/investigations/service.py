from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.audit.service import append_audit_event
from tracerisk.companies.models import Company
from tracerisk.investigations.models import (
    DispatchOutbox,
    Investigation,
    RerunRequest,
    WorkflowRun,
)
from tracerisk.workflows.events import append_workflow_event


async def create_investigation(
    session: AsyncSession, *, company_id: UUID, created_by: UUID, as_of_date: date
) -> Investigation:
    """Caller owns the transaction; no enqueue or implicit commit occurs here."""
    investigation_id, run_id = uuid4(), uuid4()
    investigation = Investigation(
        id=investigation_id,
        company_id=company_id,
        created_by=created_by,
        as_of_date=as_of_date,
        current_run_id=run_id,
    )
    session.add(investigation)
    session.add(WorkflowRun(id=run_id, investigation_id=investigation_id))
    await session.flush()
    session.add(DispatchOutbox(run_id=run_id, operation="start"))
    await session.flush()
    await append_workflow_event(
        session,
        run_id=run_id,
        event_key="run:queued",
        event_type="run.queued",
        run_status="queued",
        metadata={"run_number": 1},
    )
    return investigation


class CreationConflict(ValueError):
    """A creation key cannot identify different creation parameters."""


class CompanyNotFound(ValueError):
    """The requested company does not exist."""


class RerunConflict(ValueError):
    """The rerun key or current Run is not eligible."""


class InvestigationNotFound(ValueError):
    """The requested investigation does not exist."""


async def creation_replay(
    session: AsyncSession,
    *,
    company_id: UUID,
    created_by: UUID,
    as_of_date: date,
    idempotency_key: str,
) -> Investigation | None:
    existing = await session.scalar(
        select(Investigation).where(
            Investigation.created_by == created_by,
            Investigation.creation_idempotency_key == idempotency_key,
        )
    )
    if existing is not None and (existing.company_id, existing.as_of_date) != (
        company_id,
        as_of_date,
    ):
        raise CreationConflict("Idempotency key was used for a different request")
    return existing


async def create_authenticated_investigation(
    sessions: async_sessionmaker[AsyncSession],
    *,
    company_id: UUID,
    created_by: UUID,
    as_of_date: date,
    idempotency_key: str,
) -> Investigation:
    """Atomically commit creation and replay durable keys before eligibility checks."""
    try:
        async with sessions.begin() as session:
            previous = await creation_replay(
                session,
                company_id=company_id,
                created_by=created_by,
                as_of_date=as_of_date,
                idempotency_key=idempotency_key,
            )
            if previous is not None:
                return previous
            if await session.get(Company, company_id) is None:
                raise CompanyNotFound("Company not found")
            investigation = await create_investigation(
                session,
                company_id=company_id,
                created_by=created_by,
                as_of_date=as_of_date,
            )
            investigation.creation_idempotency_key = idempotency_key
            await session.flush()
            await append_audit_event(
                session,
                event_type="investigation.created",
                action_key=f"investigation:{investigation.id}:created",
                actor_id=created_by,
                investigation_id=investigation.id,
                run_id=investigation.current_run_id,
                resource_type="investigation",
                resource_id=investigation.id,
                metadata={"as_of_date": as_of_date.isoformat()},
            )
            return investigation
    except IntegrityError:
        # The winning transaction has committed; the losing creation is rolled back.
        async with sessions() as session:
            previous = await creation_replay(
                session,
                company_id=company_id,
                created_by=created_by,
                as_of_date=as_of_date,
                idempotency_key=idempotency_key,
            )
            if previous is not None:
                return previous
        raise


async def rerun_replay(
    session: AsyncSession,
    *,
    investigation_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
) -> RerunRequest | None:
    existing = await session.scalar(
        select(RerunRequest).where(
            RerunRequest.actor_id == actor_id,
            RerunRequest.idempotency_key == idempotency_key,
        )
    )
    if existing is not None and existing.investigation_id != investigation_id:
        raise RerunConflict("Idempotency key was used for a different investigation")
    return existing


async def create_rerun(
    sessions: async_sessionmaker[AsyncSession],
    *,
    investigation_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
) -> RerunRequest:
    try:
        async with sessions.begin() as session:
            previous = await rerun_replay(
                session,
                investigation_id=investigation_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            if previous is not None:
                return previous
            investigation = await session.scalar(
                select(Investigation)
                .where(Investigation.id == investigation_id)
                .with_for_update()
            )
            if investigation is None:
                raise InvestigationNotFound("Investigation not found")
            current_run_id = investigation.current_run_id
            current_run = await session.get(
                WorkflowRun, current_run_id, with_for_update=True
            )
            previous = await rerun_replay(
                session,
                investigation_id=investigation_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            if previous is not None:
                return previous
            if current_run is None or current_run.status not in {"completed", "failed"}:
                raise RerunConflict("Current Run is not terminal")
            run_number = await session.scalar(
                select(func.max(WorkflowRun.run_number)).where(
                    WorkflowRun.investigation_id == investigation_id
                )
            )
            new_run = WorkflowRun(
                investigation_id=investigation_id,
                run_number=(run_number or 0) + 1,
                status="queued",
            )
            session.add(new_run)
            await session.flush()
            request = RerunRequest(
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                investigation_id=investigation_id,
                run_id=new_run.id,
                run_number=new_run.run_number,
            )
            session.add_all(
                [request, DispatchOutbox(run_id=new_run.id, operation="start")]
            )
            changed = await session.scalar(
                update(Investigation)
                .where(
                    Investigation.id == investigation_id,
                    Investigation.current_run_id == current_run_id,
                )
                .values(current_run_id=new_run.id)
                .returning(Investigation.id)
            )
            if changed is None:
                raise RerunConflict("Current Run changed")
            await append_audit_event(
                session,
                event_type="investigation.rerun_created",
                action_key=f"run:{new_run.id}:rerun",
                actor_id=actor_id,
                investigation_id=investigation_id,
                run_id=new_run.id,
                resource_type="workflow_run",
                resource_id=new_run.id,
                metadata={
                    "previous_run_id": str(current_run_id),
                    "run_number": new_run.run_number,
                    "status": "queued",
                },
            )
            await session.flush()
            await append_workflow_event(
                session,
                run_id=new_run.id,
                event_key="run:queued",
                event_type="run.queued",
                run_status="queued",
                metadata={"run_number": new_run.run_number},
            )
            return request
    except IntegrityError as exc:
        diagnostic = getattr(exc.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) != "uq_rerun_actor_key":
            raise
        async with sessions() as session:
            previous = await rerun_replay(
                session,
                investigation_id=investigation_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            if previous is not None:
                return previous
        raise RerunConflict("A rerun request already won this race") from exc
