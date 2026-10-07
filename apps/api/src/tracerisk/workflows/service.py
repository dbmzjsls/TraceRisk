"""Run Outbox dispatch and Procrastinate consumption as one failure unit."""

import asyncio
import logging
import signal
import sys
from collections.abc import Coroutine
from typing import Any

import procrastinate
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.config import Settings
from tracerisk.retrieval.indexing import IndexContract, check_index
from tracerisk.workflows.health import HealthReporter
from tracerisk.workflows.outbox import dispatch_once, recover_stalled
from tracerisk.workflows.queue import QUEUE_NAME
from tracerisk.workflows.worker import create_worker

logger = logging.getLogger(__name__)
# These bounds leave time inside the worker's 30-second Compose stop window.
WORKER_SHUTDOWN_GRACE_SECONDS = 5.0
SUPERVISOR_SHUTDOWN_SECONDS = 15.0
RESOURCE_SHUTDOWN_SECONDS = 5.0


async def consume(app: procrastinate.App) -> None:
    await app.run_worker_async(
        queues=[QUEUE_NAME],
        install_signal_handlers=False,
        shutdown_graceful_timeout=WORKER_SHUTDOWN_GRACE_SECONDS,
    )


async def supervise(
    dispatcher: Coroutine[Any, Any, None],
    worker: Coroutine[Any, Any, None],
    heartbeat: Coroutine[Any, Any, None],
    stop: asyncio.Event,
) -> None:
    tasks: set[asyncio.Task[None]] = {
        asyncio.create_task(dispatcher, name="dispatcher"),
        asyncio.create_task(worker, name="worker"),
        asyncio.create_task(heartbeat, name="queue-health"),
    }
    stop_task = asyncio.create_task(stop.wait(), name="shutdown")
    try:
        completed, _ = await asyncio.wait(
            tasks | {stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in completed - {stop_task}:
            task.result()
        if stop_task not in completed:
            raise RuntimeError("A workflow responsibility exited unexpectedly")
    finally:
        for task in tasks | {stop_task}:
            task.cancel()
        _, pending = await asyncio.wait(
            tasks | {stop_task}, timeout=SUPERVISOR_SHUTDOWN_SECONDS
        )
        if pending:
            for task in pending:
                task.cancel()
            _, pending = await asyncio.wait(pending, timeout=1)
            if pending:
                raise RuntimeError("Workflow service shutdown timed out")


async def serve() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    reporter = HealthReporter()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    try:
        async with sessions() as session:
            await check_index(session, IndexContract.from_settings(settings))
        async with asyncio.timeout(None) as queue_close_timeout:
            async with create_worker(settings, sessions).open_async() as app:

                async def dispatch() -> None:
                    while True:
                        await recover_stalled(app)
                        await dispatch_once(sessions, app)
                        reporter.mark_dispatcher()
                        await asyncio.sleep(2)

                async def heartbeat() -> None:
                    while True:
                        await app.connector.execute_query_all_async("SELECT 1")
                        reporter.mark_worker()
                        await asyncio.sleep(2)

                try:
                    await supervise(dispatch(), consume(app), heartbeat(), stop)
                finally:
                    queue_close_timeout.reschedule(
                        loop.time() + RESOURCE_SHUTDOWN_SECONDS
                    )
    finally:
        reporter.close()
        try:
            async with asyncio.timeout(RESOURCE_SHUTDOWN_SECONDS):
                await engine.dispose()
        finally:
            if sys.platform != "win32":
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(serve())
