import asyncio
import logging
import sys

import procrastinate
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.config import Settings
from tracerisk.investigations.models import DispatchOutbox
from tracerisk.workflows.queue import QUEUE_NAME, TASK_NAME, create_queue, enqueue_run

logger = logging.getLogger(__name__)


async def dispatch_once(
    sessions: async_sessionmaker[AsyncSession], app: procrastinate.App, limit: int = 100
) -> int:
    dispatched = 0
    for _ in range(limit):
        async with sessions.begin() as session:
            item = await session.scalar(
                select(DispatchOutbox)
                .where(DispatchOutbox.dispatched_at.is_(None))
                .order_by(DispatchOutbox.created_at, DispatchOutbox.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if item is None:
                break
            await enqueue_run(app, item.run_id, item.operation)
            acknowledged = await session.scalar(
                update(DispatchOutbox)
                .where(
                    DispatchOutbox.id == item.id,
                    DispatchOutbox.dispatched_at.is_(None),
                )
                .values(dispatched_at=func.now())
                .returning(DispatchOutbox.id)
            )
            if acknowledged is None:
                raise RuntimeError("Outbox acknowledgement conflict")
        dispatched += 1
    return dispatched


async def recover_stalled(app: procrastinate.App) -> int:
    jobs = await app.job_manager.get_stalled_jobs(
        queue=QUEUE_NAME, task_name=TASK_NAME, seconds_since_heartbeat=30
    )
    count = 0
    for job in jobs:
        await app.job_manager.retry_job(job)
        count += 1
    return count


async def serve() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with create_queue(settings).open_async() as app:
            while True:
                try:
                    await recover_stalled(app)
                    await dispatch_once(async_sessionmaker(engine), app)
                except Exception:
                    logger.exception("Dispatch scan failed; retrying on the next scan")
                await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(serve())
