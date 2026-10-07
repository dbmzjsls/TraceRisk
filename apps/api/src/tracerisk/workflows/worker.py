import asyncio
import sys
from uuid import UUID

import procrastinate
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.audit.service import append_audit_event
from tracerisk.config import Settings
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.retrieval.indexing import IndexContract, check_index
from tracerisk.workflows.errors import PermanentWorkflowError
from tracerisk.workflows.events import append_workflow_event
from tracerisk.workflows.queue import QUEUE_NAME, TASK_NAME, create_queue
from tracerisk.workflows.spike_graph import execute_workflow


async def execute_probe(
    sessions: async_sessionmaker[AsyncSession], run_id: UUID, attempt_count: int = 1
) -> None:
    async with sessions.begin() as session:
        investigation = await session.scalar(
            select(Investigation)
            .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
            .where(WorkflowRun.id == run_id)
            .with_for_update(of=Investigation)
        )
        run = await session.get(WorkflowRun, run_id, with_for_update=True)
        if (
            investigation is None
            or run is None
            or investigation.current_run_id != run_id
            or run.status not in {"queued", "running"}
        ):
            return
        existing = await session.scalar(
            select(WorkflowStep.id).where(
                WorkflowStep.run_id == run_id, WorkflowStep.name == "dispatch_probe"
            )
        )
        if existing is not None:
            return
        if run.status == "queued":
            changed = await session.scalar(
                update(WorkflowRun)
                .where(WorkflowRun.id == run_id, WorkflowRun.status == "queued")
                .values(status="running")
                .returning(WorkflowRun.id)
            )
            if changed is None:
                raise RuntimeError("Run state conflict")
            await append_workflow_event(
                session,
                run_id=run_id,
                event_key="run:running:start",
                event_type="run.running",
                run_status="running",
                metadata={"attempt_count": attempt_count},
            )
        # Both rows remain locked through the result commit: validity cannot change.
        session.add(
            WorkflowStep(
                run_id=run_id, name="dispatch_probe", attempt_count=attempt_count
            )
        )
        # No report exists yet: leave the Run running for the next spike stage.


async def mark_run_failed(
    sessions: async_sessionmaker[AsyncSession], run_id: UUID
) -> bool:
    async with sessions.begin() as session:
        investigation = await session.scalar(
            select(Investigation)
            .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
            .where(
                WorkflowRun.id == run_id,
                Investigation.current_run_id == run_id,
            )
            .with_for_update(of=Investigation)
        )
        if investigation is None:
            return False
        changed = await session.scalar(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.status.in_(["queued", "running", "resume_queued"]),
            )
            .values(status="failed")
            .returning(WorkflowRun.id)
        )
        if changed is None:
            return False
        await append_audit_event(
            session,
            event_type="workflow.failed",
            action_key=f"run:{run_id}:failed",
            actor_id=None,
            investigation_id=investigation.id,
            run_id=run_id,
            resource_type="workflow_run",
            resource_id=run_id,
            metadata={"status": "failed"},
        )
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="run:failed",
            event_type="run.failed",
            run_status="failed",
        )
        return True


def create_worker(
    settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> procrastinate.App:
    app = create_queue(settings)
    retry = procrastinate.RetryStrategy(max_attempts=2, wait=1)

    @app.task(
        name=TASK_NAME,
        queue=QUEUE_NAME,
        pass_context=True,
        retry=retry,
    )
    async def dispatch_probe(
        context: procrastinate.JobContext, run_id: str, operation: str = "start"
    ) -> None:
        try:
            if operation == "start":
                await execute_probe(sessions, UUID(run_id), context.job.attempts + 1)
            await execute_workflow(settings, sessions, UUID(run_id), operation)
        except PermanentWorkflowError:
            await mark_run_failed(sessions, UUID(run_id))
            return
        except Exception as exc:
            if retry.get_retry_decision(exception=exc, job=context.job) is None:
                await mark_run_failed(sessions, UUID(run_id))
            raise

    return app


async def serve() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine)() as session:
            await check_index(session, IndexContract.from_settings(settings))
        async with create_worker(
            settings, async_sessionmaker(engine)
        ).open_async() as app:
            await app.run_worker_async(queues=[QUEUE_NAME])
    finally:
        await engine.dispose()


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(serve())
