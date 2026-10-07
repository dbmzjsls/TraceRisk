import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import httpx
import procrastinate
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.claims import ClaimFact, canonical_statement
from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.models import AIFinding
from tracerisk.ai.schemas import RiskContext, StructuredRiskFindings
from tracerisk.auth.models import User
from tracerisk.auth.seed import DEMO_ACCOUNTS, seed_accounts
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.investigations.models import DispatchOutbox, WorkflowRun
from tracerisk.investigations.service import create_investigation
from tracerisk.main import app
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.models import RuleFinding
from tracerisk.workflows.outbox import dispatch_once
from tracerisk.workflows.queue import enqueue_run
from tracerisk.workflows.spike_graph import build_graph, initialize_checkpoints
from tracerisk.workflows.worker import create_worker

pytestmark = pytest.mark.anyio
Resources = tuple[
    Settings,
    async_sessionmaker[AsyncSession],
    procrastinate.App,
    httpx.AsyncClient,
    dict[str, dict[str, str]],
    UUID,
]


@pytest.fixture
async def resources(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Resources]:
    monkeypatch.setenv(
        "JWT_SECRET", "e2e-demo-secret-at-least-48-characters-long-for-testing"
    )
    await initialize_checkpoints()
    # Load the real repository seed script without changing production imports.
    path = Path(__file__).resolve().parents[4] / "scripts" / "seed_synthetic.py"
    spec = importlib.util.spec_from_file_location("synthetic_story", path)
    assert spec is not None and spec.loader is not None
    story = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(story)
    async with app.router.lifespan_context(app):
        sessions = app.state.session_factory
        async with sessions() as session:
            await seed_accounts(session)
            await story.seed_synthetic(session)
        async with sessions.begin() as session:
            await build_index(session, FakeEmbedding(), IndexContract.fake())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {}
            for username, password, _ in DEMO_ACCOUNTS:
                response = await client.post(
                    "/auth/login", json={"username": username, "password": password}
                )
                assert response.status_code == 200
                headers[username] = {
                    "Authorization": f"Bearer {response.json()['access_token']}"
                }
            async with sessions.begin() as session:
                analyst = await session.scalar(
                    select(User).where(User.username == "analyst")
                )
                assert analyst is not None
                investigation = await create_investigation(
                    session,
                    company_id=story.COMPANY_ID,
                    created_by=analyst.id,
                    as_of_date=date(2026, 9, 11),
                )
                run_id = investigation.current_run_id
            async with create_worker(database, sessions).open_async() as queue:
                await queue.schema_manager.apply_schema_async()
                yield database, sessions, queue, client, headers, run_id


async def worker_process() -> None:
    script = """
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from tracerisk.config import Settings
from tracerisk.workflows.worker import create_worker
async def main():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        worker = create_worker(settings, async_sessionmaker(engine))
        async with worker.open_async() as queue:
            await queue.run_worker_async(wait=False, install_signal_handlers=False,
                                         queues=['tracerisk'])
    finally:
        await engine.dispose()
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def saved_finding(
    sessions: async_sessionmaker[AsyncSession], run_id: UUID
) -> AIFinding:
    async with sessions() as session:
        rules = (
            await session.scalars(
                select(RuleFinding).where(RuleFinding.run_id == run_id)
            )
        ).all()
        assert len(rules) == 1 and rules[0].score == 35
        findings = (
            await session.scalars(select(AIFinding).where(AIFinding.run_id == run_id))
        ).all()
        assert len(findings) == 1
        finding = findings[0]
        assert len(finding.citations) == 2
        chunks = (
            await session.scalars(
                select(EvidenceChunk)
                .join(RetrievedEvidence, RetrievedEvidence.chunk_id == EvidenceChunk.id)
                .where(RetrievedEvidence.run_id == run_id)
            )
        ).all()
        # Hybrid retrieval freezes three source versions; the Mock and rule engine
        # independently deduplicate their two judicial case keys.
        assert len(chunks) == 3
        assert {chunk.fact_key for chunk in chunks} == {"SYN-CASE-001", "SYN-CASE-002"}
        saved = {str(chunk.id): chunk for chunk in chunks}
        for citation in finding.citations:
            chunk = saved[citation["chunk_id"]]
            evidence = await session.get(Evidence, chunk.evidence_id)
            assert evidence is not None
            assert citation["evidence_id"] == str(evidence.id)
            assert (
                citation["content_hash"] == chunk.content_hash == evidence.content_hash
            )
            assert sha256(chunk.content.encode()).hexdigest() == chunk.content_hash
            assert (
                sha256(evidence.content.encode()).hexdigest() == evidence.content_hash
            )
            assert citation["quoted_excerpt"] in chunk.content
            assert citation["quoted_excerpt"] in evidence.content
        return finding


async def test_persisted_review_new_process_resume_and_replay(
    resources: Resources,
) -> None:
    settings, sessions, queue, client, headers, run_id = resources
    assert await dispatch_once(sessions, queue) == 1
    await worker_process()
    finding = await saved_finding(sessions, run_id)
    assert finding.review_recommended
    assert finding.support_status == "indeterminate"
    assert finding.support_reason == "unverified_free_text_extension"
    reasons = [
        "model_evidence_insufficient",
        "model_recommended",
        "unverified_free_text_extension",
    ]
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "awaiting_review"
        assert await session.scalar(
            text("SELECT DISTINCT thread_id FROM checkpoints")
        ) == str(run_id)
    conninfo = settings.database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        state = await build_graph(saver, sessions).aget_state(
            {"configurable": {"thread_id": str(run_id)}}
        )
        interrupts = [item for task in state.tasks for item in task.interrupts]
        assert len(interrupts) == 1
        assert interrupts[0].value == {
            "run_id": str(run_id),
            "reason": reasons,
        }
    review_headers = {**headers["reviewer"], "Idempotency-Key": "story-approval"}
    payload = {
        "decision": "approve",
        "finding_version": finding.version,
        "reason": "已核对两案合成证据",
    }
    response = await client.post(
        f"/reviews/{finding.id}", headers=review_headers, json=payload
    )
    assert response.status_code == 200
    decision_id = response.json()["decision_id"]
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "resume_queued"
    assert await dispatch_once(sessions, queue) == 1
    await worker_process()
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "completed"
        report = await session.scalar(select(RiskReport))
        assert report is not None
        report_id = report.id
    snapshots = []
    for role in ("analyst", "reviewer", "admin"):
        response = await client.get(f"/reports/{report_id}", headers=headers[role])
        assert response.status_code == 200
        snapshots.append(response.json())
    assert snapshots[0] == snapshots[1] == snapshots[2]
    snapshot = snapshots[0]["snapshot"]
    html = snapshots[0]["html_content"].encode("utf-8")
    snapshot_bytes = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
    assert snapshot["rule_score"] == 35
    assert snapshot["notice"] == "合成数据，仅供演示"
    assert "合成数据，仅供演示".encode() in html
    assert snapshot["review_reasons"] == reasons
    original = snapshot["ai_findings"]
    assert len(original) == 1 and original[0]["id"] == str(finding.id)
    for field in (
        "finding_key",
        "version",
        "risk_type",
        "claim_code",
        "title",
        "summary",
        "reasoning",
        "citations",
        "evidence_sufficiency",
        "confidence_level",
        "review_recommended",
        "support_status",
        "support_reason",
    ):
        assert original[0][field] == getattr(finding, field)
    assert snapshot["final_findings"] == [
        {
            **original[0],
            "source": "ai",
            "human_version": None,
            "original_ai_finding_id": original[0]["id"],
        }
    ]
    assert len(snapshot["human_decisions"]) == 1
    assert snapshot["human_decisions"][0]["id"] == decision_id
    assert snapshot["human_decisions"][0]["decision"] == "approve"
    response = await client.post(
        f"/reviews/{finding.id}", headers=review_headers, json=payload
    )
    assert response.status_code == 200 and response.json()["decision_id"] == decision_id
    assert await dispatch_once(sessions, queue) == 0
    await enqueue_run(queue, run_id, "resume")
    await worker_process()
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(DispatchOutbox.operation == "resume")
            )
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1
        assert await session.scalar(select(WorkflowRun.status)) == "completed"
    response = await client.get(f"/reports/{report_id}", headers=headers["analyst"])
    assert response.status_code == 200
    assert (
        json.dumps(
            response.json()["snapshot"], ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        == snapshot_bytes
    )
    assert response.json()["html_content"].encode("utf-8") == html


async def test_score35_graph_direct_report(
    resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sessions, queue, client, headers, run_id = resources
    original = MockModelAdapter.analyze_risk

    async def direct(
        self: MockModelAdapter, context: RiskContext
    ) -> StructuredRiskFindings:
        result = await original(self, context)
        by_id = {chunk.chunk_id: chunk for chunk in context.evidence_chunks}
        findings = []
        for finding in result.findings:
            wording = "\n".join(
                canonical_statement(
                    ClaimFact(
                        chunk.risk_type,
                        "judicial",  # This fixture contains only judicial sources.
                        chunk.fact_type,
                        chunk.fact_key,
                        chunk.fact_value,
                        chunk.polarity,
                    )
                )
                for citation in finding.evidence_citations
                for chunk in [by_id[citation.chunk_id]]
            )
            findings.append(
                finding.model_copy(
                    update={
                        "title": wording,
                        "summary": wording,
                        "reasoning": wording,
                        "review_recommended": False,
                    }
                )
            )
        return result.model_copy(update={"findings": findings})

    monkeypatch.setattr(MockModelAdapter, "analyze_risk", direct)
    assert await dispatch_once(sessions, queue) == 1
    await queue.run_worker_async(
        wait=False, install_signal_handlers=False, queues=["tracerisk"]
    )
    finding = await saved_finding(sessions, run_id)
    assert not finding.review_recommended
    assert finding.support_status == "supported"
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "completed"
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 0
        )
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1
        report = await session.scalar(select(RiskReport))
        assert report is not None
        assert report.snapshot["rule_score"] == 35
        assert report.snapshot["review_reasons"] == []
        assert report.snapshot["human_decisions"] == []
        ai_item = report.snapshot["ai_findings"][0]
        assert report.snapshot["final_findings"] == [
            {
                **ai_item,
                "source": "ai",
                "human_version": None,
                "original_ai_finding_id": ai_item["id"],
            }
        ]
    response = await client.get(f"/reports/{report.id}", headers=headers["analyst"])
    assert response.status_code == 200
