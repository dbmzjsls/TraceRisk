import asyncio
import sys
from uuid import UUID

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued

from tracerisk.config import Settings

TASK_NAME = "tracerisk.dispatch_probe"
QUEUE_NAME = "tracerisk"


def create_queue(settings: Settings) -> procrastinate.App:
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=settings.database_url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            min_size=1,
            max_size=4,
        )
    )


async def enqueue_run(app: procrastinate.App, run_id: UUID, operation: str) -> None:
    if operation not in {"start", "resume"}:
        raise ValueError("Unknown workflow operation")
    key = f"run:{run_id}:{operation}"
    active = await app.connector.execute_query_all_async(
        "SELECT id FROM procrastinate_jobs "
        "WHERE queueing_lock = %(key)s AND status IN ('todo', 'doing') LIMIT 1",
        key=key,
    )
    if active:
        return
    try:
        await app.configure_task(
            TASK_NAME, queue=QUEUE_NAME, lock=f"run:{run_id}", queueing_lock=key
        ).defer_async(run_id=str(run_id), operation=operation)
    except AlreadyEnqueued:
        # An earlier scan committed the job but did not acknowledge the outbox.
        return


async def initialize_schema() -> None:
    """Explicit first-time queue setup, separate from API and worker startup."""
    async with create_queue(Settings()).open_async() as app:
        await app.schema_manager.apply_schema_async()


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(initialize_schema())
