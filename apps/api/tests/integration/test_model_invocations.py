import asyncio
import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.test_stage4_workflow_retrieval import seed
from tracerisk.ai.invocations import DatabaseInvocationRecorder
from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.models import AIFinding, ModelInvocation
from tracerisk.ai.openai_compatible import OpenAICompatibleAdapter
from tracerisk.audit.models import AuditEvent
from tracerisk.evidence.service import (
    _saved_rows,
    _snapshot_manifest,
    build_risk_context,
    retrieve_evidence,
)
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.retrieval.models import EmbeddingIndexVersion
from tracerisk.workflows.errors import PermanentWorkflowError
from tracerisk.workflows.outbox import dispatch_once
from tracerisk.workflows.spike_graph import execute_workflow, initialize_checkpoints
from tracerisk.workflows.worker import create_worker

pytestmark = pytest.mark.anyio


@pytest.fixture
def local_model():
    replies = deque()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            status, body = replies.popleft()
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if isinstance(body, bytes):
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def response(findings):
    return {
        "choices": [{"message": {"content": json.dumps(findings)}}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 9},
    }


@pytest.fixture
async def model_resources(database, local_model):
    url, replies, requests = local_model
    settings = database.model_copy(
        update={
            "model_adapter": "openai_compatible",
            "model_base_url": url,
            "model_name": "local-test",
            "model_api_key": SecretStr("test-secret-never-store"),
        }
    )
    # Every business/recorder connection gets bounded lock and statement timeouts.
    engine = create_async_engine(
        database.database_url,
        connect_args={"options": "-c lock_timeout=1200 -c statement_timeout=3500"},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await initialize_checkpoints()
        async with sessions.begin() as session:
            run_id, _ = await seed(session)
            await retrieve_evidence(session, run_id=run_id)
            context = await build_risk_context(session, run_id=run_id)
        draft = (await MockModelAdapter().analyze_risk(context)).model_dump(mode="json")
        yield settings, sessions, run_id, draft, replies, requests
    finally:
        await engine.dispose()


async def test_graph_records_without_row_lock_deadlock(model_resources):
    settings, sessions, run_id, draft, replies, requests = model_resources
    replies.append((200, response(draft)))
    await asyncio.wait_for(execute_workflow(settings, sessions, run_id, "start"), 12)
    async with sessions() as session:
        rows = (await session.scalars(select(ModelInvocation))).all()
        assert len(rows) == 1 and rows[0].run_id == run_id
        assert (rows[0].input_tokens, rows[0].output_tokens) == (17, 9)
        assert rows[0].status == "succeeded"
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 1
        assert (await session.get(WorkflowRun, run_id)).status == "awaiting_review"
        saved = (
            await session.execute(
                text("SELECT row_to_json(model_invocations) FROM model_invocations")
            )
        ).scalar_one()
        serialized = json.dumps(saved)
        for forbidden in (
            "test-secret",
            "Authorization",
            "messages",
            "choices",
            "evidence_citations",
            draft["findings"][0]["reasoning"],
            draft["findings"][0]["evidence_citations"][0]["quoted_excerpt"],
        ):
            assert forbidden not in serialized
    assert len(requests) == 1


async def test_independent_logical_calls_keep_noncolliding_attempt_rows(
    model_resources,
):
    settings, sessions, run_id, draft, replies, requests = model_resources
    replies.extend([(503, {}), (200, response(draft))] * 2)
    async with sessions.begin() as session:
        context = await build_risk_context(session, run_id=run_id)
    async with httpx.AsyncClient(trust_env=False) as client:
        adapter = OpenAICompatibleAdapter(
            client,
            base_url=settings.model_base_url,
            model=settings.model_name,
            api_key=settings.model_api_key,
            run_id=run_id,
            recorder=DatabaseInvocationRecorder(sessions),
        )
        await adapter.analyze_risk(context)
        await adapter.analyze_risk(context)
    async with sessions() as session:
        rows = (await session.scalars(select(ModelInvocation))).all()
        assert len(rows) == 4
        ids = {r.logical_call_id for r in rows}
        assert len(ids) == 2
        for call_id in ids:
            assert {r.attempt_number for r in rows if r.logical_call_id == call_id} == {
                1,
                2,
            }
        assert len({r.id for r in rows}) == 4
    assert len(requests) == 4


@pytest.mark.parametrize(
    "failure,attempts",
    [
        ("server", 2),
        ("timeout", 2),
        ("network", 2),
        ("decoding", 2),
        ("permanent", 1),
        ("schema", 2),
        ("citation", 1),
        ("recorder", 1),
        ("rollback", 2),
    ],
)
async def test_queue_terminal_failure_has_no_retry(
    model_resources, monkeypatch, failure, attempts
):
    settings, sessions, run_id, draft, replies, requests = model_resources
    transport_calls = []
    if failure in {"timeout", "network"}:
        import httpx

        async def failed_post(self, *args, **kwargs):
            transport_calls.append(1)
            error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
            raise error("secret upstream error")

        monkeypatch.setattr(httpx.AsyncClient, "post", failed_post)
    elif failure == "server":
        replies.extend([(503, {"secret": "upstream"})] * 2)
    elif failure == "decoding":
        replies.extend([(503, {}), (200, b"not-a-valid-gzip-stream")])
    elif failure == "permanent":
        replies.append((401, {"secret": "upstream"}))
    elif failure == "schema":
        replies.extend([(200, response({"score": 100}))] * 2)
    elif failure == "citation":
        draft["findings"][0]["evidence_citations"][0]["chunk_id"] = str(uuid4())
        replies.append((200, response(draft)))
    elif failure == "rollback":
        # First finding INSERT succeeds; duplicate second key forces rollback.
        draft["findings"] *= 2
        replies.extend([(503, {}), (200, response(draft))])
    else:
        replies.append((503, {}))

        async def failed_record(self, attempt):
            raise RuntimeError("database secret")

        monkeypatch.setattr(DatabaseInvocationRecorder, "__call__", failed_record)
    async with create_worker(settings, sessions).open_async() as app:
        await app.schema_manager.apply_schema_async()
        await dispatch_once(sessions, app)
        await asyncio.wait_for(
            app.run_worker_async(
                wait=False, install_signal_handlers=False, queues=["tracerisk"]
            ),
            15,
        )
        # Another worker poll cannot start a second logical model call.
        await app.run_worker_async(
            wait=False, install_signal_handlers=False, queues=["tracerisk"]
        )
    async with sessions() as session:
        assert (await session.get(WorkflowRun, run_id)).status == "failed"
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkflowStep)
                .where(WorkflowStep.name == "generate_validate_findings")
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "workflow.failed")
            )
            == 1
        )
        job = (
            await session.execute(
                text("SELECT status, attempts FROM procrastinate_jobs")
            )
        ).one()
        assert job.status == "succeeded" and job.attempts == 1
        rows = (
            await session.scalars(
                select(ModelInvocation).order_by(ModelInvocation.attempt_number)
            )
        ).all()
        assert len(rows) == (0 if failure == "recorder" else attempts)
        if rows:
            assert len({r.logical_call_id for r in rows}) == 1
            assert [r.attempt_number for r in rows] == list(range(1, attempts + 1))
            assert all(r.run_id == run_id for r in rows)
            if failure == "decoding":
                assert [r.error_category for r in rows] == [
                    "server",
                    "structured_output",
                ]
    assert len(requests) + len(transport_calls) == attempts


@pytest.mark.parametrize(
    "change", ["stale", "status", "fingerprint", "valid_replacement"]
)
async def test_second_transaction_rejects_changed_run_or_manifest(
    model_resources, monkeypatch, change
):
    settings, sessions, run_id, draft, replies, requests = model_resources
    replies.append((200, response(draft)))
    original = DatabaseInvocationRecorder.__call__

    async def record_then_change(self, attempt):
        await original(self, attempt)
        async with sessions.begin() as session:
            if change == "stale":
                await session.execute(update(Investigation).values(current_run_id=None))
            elif change == "status":
                await session.execute(update(WorkflowRun).values(status="failed"))
            elif change == "valid_replacement":
                # Re-sign an internally valid manifest with unchanged context. The
                # exact pre-HTTP fingerprint, not mere validity, must be checked.
                investigation = await session.scalar(select(Investigation))
                version = await session.scalar(select(EmbeddingIndexVersion))
                version.version_key = "replaced-during-model-call"
                await session.flush()
                manifest = _snapshot_manifest(
                    investigation, version, await _saved_rows(session, run_id)
                )
                await session.execute(
                    update(WorkflowStep)
                    .where(WorkflowStep.name.startswith("retrieve_evidence_manifest:"))
                    .values(name=manifest)
                )
                # This context still passes frozen-set validation.
                await build_risk_context(session, run_id=run_id)
            else:
                await session.execute(
                    update(WorkflowStep)
                    .where(WorkflowStep.name.startswith("retrieve_evidence_manifest:"))
                    .values(name="retrieve_evidence_manifest:changed")
                )

    monkeypatch.setattr(DatabaseInvocationRecorder, "__call__", record_then_change)
    with pytest.raises((ValueError, PermanentWorkflowError)) as error:
        await asyncio.wait_for(
            execute_workflow(settings, sessions, run_id, "start"), 12
        )
    if change == "valid_replacement":
        assert "Frozen retrieval changed" in str(error.value)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        )
    assert len(requests) == 1
