import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import httpx
import procrastinate
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.service import PermanentWorkflowError
from tracerisk.audit import service as audit_service
from tracerisk.audit.models import AuditEvent
from tracerisk.auth.models import User
from tracerisk.auth.seed import seed_accounts
from tracerisk.auth.service import create_access_token
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import SourceCoverage
from tracerisk.evidence.service import retrieve_evidence
from tracerisk.investigations import service as investigation_service
from tracerisk.investigations.models import (
    DispatchOutbox,
    Investigation,
    RerunRequest,
    WorkflowRun,
)
from tracerisk.investigations.service import create_investigation
from tracerisk.main import app
from tracerisk.reports import service as report_service
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews import service as review_service
from tracerisk.reviews.models import ReviewDecision
from tracerisk.workflows import worker
from tracerisk.workflows.outbox import dispatch_once
from tracerisk.workflows.queue import QUEUE_NAME, enqueue_run

SECRET = "audit-rerun-test-secret-at-least-48-characters"
pytestmark = pytest.mark.anyio
WorkerResources = tuple[async_sessionmaker[AsyncSession], procrastinate.App]


@pytest.fixture
async def client(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            await seed_accounts(session)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as connection:
            yield connection


async def identity(username: str) -> tuple[UUID, dict[str, str]]:
    async with app.state.session_factory() as session:
        user = await session.scalar(select(User).where(User.username == username))
        assert user is not None
        return user.id, {
            "Authorization": f"Bearer {create_access_token(user.id, SECRET)}"
        }


async def company() -> UUID:
    async with app.state.session_factory.begin() as session:
        row = Company(
            synthetic_key=str(uuid4()),
            name="Audit rerun company",
            unified_credit_code=str(uuid4()),
        )
        session.add(row)
        await session.flush()
        return row.id


async def create_terminal(owner: UUID, status: str = "completed") -> Investigation:
    async with app.state.session_factory.begin() as session:
        investigation = await create_investigation(
            session,
            company_id=await company(),
            created_by=owner,
            as_of_date=date(2026, 9, 11),
        )
        run = await session.get(WorkflowRun, investigation.current_run_id)
        assert run is not None
        run.status = status
        if status == "running":
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            await retrieve_evidence(session, run_id=run.id)
        return investigation


async def rerun(
    client: httpx.AsyncClient,
    investigation_id: UUID,
    headers: dict[str, str],
    key: str = "rerun-1",
) -> httpx.Response:
    return await client.post(
        f"/investigations/{investigation_id}/runs",
        headers={**headers, "Idempotency-Key": key},
    )


async def consume(worker_app: procrastinate.App) -> None:
    await worker_app.run_worker_async(
        queues=[QUEUE_NAME], wait=False, install_signal_handlers=False
    )


@pytest.fixture
async def worker_resources(
    client: httpx.AsyncClient, database: Settings
) -> AsyncIterator[WorkerResources]:
    del client
    sessions = app.state.session_factory
    async with worker.create_worker(database, sessions).open_async() as worker_app:
        await worker_app.schema_manager.apply_schema_async()
        yield sessions, worker_app


async def test_rerun_auth_bounds_and_terminal_states(client: httpx.AsyncClient) -> None:
    analyst_id, analyst = await identity("analyst")
    _, reviewer = await identity("reviewer")
    investigation = await create_terminal(analyst_id)
    assert (await rerun(client, investigation.id, {})).status_code == 401
    assert (await rerun(client, investigation.id, reviewer)).status_code == 403
    assert (await rerun(client, uuid4(), analyst)).status_code == 404
    for key in ("", "x" * 201):
        assert (await rerun(client, investigation.id, analyst, key)).status_code == 422
    for status in ("queued", "running", "awaiting_review", "resume_queued"):
        blocked = await create_terminal(analyst_id, status)
        assert (
            await rerun(client, blocked.id, analyst, f"blocked-{status}")
        ).status_code == 409


@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_rerun_success_replay_and_old_rows_unchanged(
    client: httpx.AsyncClient, terminal: str
) -> None:
    actor_id, analyst = await identity("analyst")
    investigation = await create_terminal(actor_id, terminal)
    old_run_id = investigation.current_run_id
    first = await rerun(client, investigation.id, analyst)
    assert first.status_code == 200
    assert first.json()["run_number"] == 2
    assert first.json()["status"] == "queued"
    new_run_id = UUID(first.json()["id"])
    async with app.state.session_factory.begin() as session:
        new_run = await session.get(WorkflowRun, new_run_id)
        old_run = await session.get(WorkflowRun, old_run_id)
        persisted = await session.get(Investigation, investigation.id)
        assert new_run is not None and old_run is not None and persisted is not None
        assert old_run.status == terminal
        assert persisted.current_run_id == new_run_id
        new_run.status = "completed"
    replay = await rerun(client, investigation.id, analyst)
    assert replay.status_code == 200 and replay.json() == first.json()
    async with app.state.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 2
        assert await session.scalar(select(func.count()).select_from(RerunRequest)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(
                    DispatchOutbox.run_id == new_run_id,
                    DispatchOutbox.operation == "start",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "investigation.rerun_created")
            )
            == 1
        )


async def test_rerun_key_target_conflict_and_four_way_concurrency(
    client: httpx.AsyncClient,
) -> None:
    actor_id, analyst = await identity("analyst")
    first_target = await create_terminal(actor_id)
    second_target = await create_terminal(actor_id)
    async with app.state.session_factory() as session:
        before = set(
            await session.scalars(
                select(WorkflowRun.id).where(
                    WorkflowRun.investigation_id == first_target.id
                )
            )
        )
    responses = await asyncio.gather(
        *[rerun(client, first_target.id, analyst, "concurrent") for _ in range(4)]
    )
    assert [response.status_code for response in responses] == [200] * 4
    assert len({response.text for response in responses}) == 1
    assert (
        await rerun(client, second_target.id, analyst, "concurrent")
    ).status_code == 409
    created_run_id = UUID(responses[0].json()["id"])
    async with app.state.session_factory() as session:
        after = set(
            await session.scalars(
                select(WorkflowRun.id).where(
                    WorkflowRun.investigation_id == first_target.id
                )
            )
        )
        persisted = await session.get(Investigation, first_target.id)
        old_run = await session.get(WorkflowRun, first_target.current_run_id)
        assert after - before == {created_run_id}
        assert len(after) == len(before) + 1 == 2
        assert persisted is not None and persisted.current_run_id == created_run_id
        assert old_run is not None and old_run.status == "completed"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RerunRequest)
                .where(
                    RerunRequest.actor_id == actor_id,
                    RerunRequest.idempotency_key == "concurrent",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(DispatchOutbox.run_id == created_run_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action_key == f"run:{created_run_id}:rerun")
            )
            == 1
        )


async def test_0009_database_constraints_and_postgres_types(
    client: httpx.AsyncClient,
) -> None:
    actor_id, _ = await identity("analyst")
    investigation = await create_terminal(actor_id)
    run_id = investigation.current_run_id
    valid = AuditEvent(
        event_type="workflow.failed",
        action_key="constraint:valid",
        actor_id=actor_id,
        investigation_id=investigation.id,
        run_id=run_id,
        resource_type="workflow_run",
        resource_id=run_id,
        metadata_json={"nested": {"count": 1}, "items": ["a", "b"]},
    )
    async with app.state.session_factory.begin() as session:
        session.add(valid)
        await session.flush()
        valid_id = valid.id
    async with app.state.session_factory() as session:
        saved = await session.get(AuditEvent, valid_id)
        assert saved is not None
        assert (
            saved.occurred_at is not None and saved.occurred_at.utcoffset() is not None
        )
        assert saved.metadata_json == {"nested": {"count": 1}, "items": ["a", "b"]}
        assert (
            await session.scalar(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'audit_events' "
                    "AND column_name = 'metadata'"
                )
            )
            == "jsonb"
        )

    def audit(
        *,
        event_type: str = "workflow.failed",
        action_key: str | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_type=event_type,
            action_key=action_key or str(uuid4()),
            actor_id=actor_id,
            investigation_id=investigation.id,
            run_id=run_id,
            resource_type="workflow_run",
            resource_id=run_id,
            metadata_json={},
        )

    with pytest.raises(IntegrityError):
        async with app.state.session_factory.begin() as session:
            session.add(audit(event_type="unapproved.event"))
    with pytest.raises(IntegrityError):
        async with app.state.session_factory.begin() as session:
            session.add(audit(action_key="constraint:valid"))

    async with app.state.session_factory.begin() as session:
        run_two = WorkflowRun(investigation_id=investigation.id, run_number=2)
        run_three = WorkflowRun(investigation_id=investigation.id, run_number=3)
        session.add_all([run_two, run_three])
        await session.flush()
        session.add(
            RerunRequest(
                actor_id=actor_id,
                idempotency_key="constraint-key",
                investigation_id=investigation.id,
                run_id=run_two.id,
                run_number=2,
            )
        )
        await session.flush()
        run_two_id, run_three_id = run_two.id, run_three.id
    with pytest.raises(IntegrityError):
        async with app.state.session_factory.begin() as session:
            session.add(
                RerunRequest(
                    actor_id=actor_id,
                    idempotency_key="constraint-key",
                    investigation_id=investigation.id,
                    run_id=run_three_id,
                    run_number=3,
                )
            )
    with pytest.raises(IntegrityError):
        async with app.state.session_factory.begin() as session:
            session.add(
                RerunRequest(
                    actor_id=actor_id,
                    idempotency_key="another-key",
                    investigation_id=investigation.id,
                    run_id=run_two_id,
                    run_number=2,
                )
            )
    with pytest.raises(IntegrityError):
        async with app.state.session_factory.begin() as session:
            session.add(
                RerunRequest(
                    actor_id=uuid4(),
                    idempotency_key="missing-actor",
                    investigation_id=investigation.id,
                    run_id=run_three_id,
                    run_number=3,
                )
            )


async def test_authenticated_creation_audit_is_allowlisted_and_legacy_is_not(
    client: httpx.AsyncClient,
) -> None:
    actor_id, analyst = await identity("analyst")
    target = await company()
    response = await client.post(
        "/investigations",
        headers={**analyst, "Idempotency-Key": "secret-idempotency-value"},
        json={"company_id": str(target), "as_of_date": "2026-09-11"},
    )
    assert response.status_code == 200
    investigation_id = UUID(response.json()["id"])
    replay = await client.post(
        "/investigations",
        headers={**analyst, "Idempotency-Key": "secret-idempotency-value"},
        json={"company_id": str(target), "as_of_date": "2026-09-11"},
    )
    assert replay.status_code == 200 and replay.json() == response.json()
    async with app.state.session_factory.begin() as session:
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.investigation_id == investigation_id)
        )
        assert event is not None
        assert event.actor_id == actor_id
        assert event.event_type == "investigation.created"
        assert event.metadata_json == {"as_of_date": "2026-09-11"}
        assert "secret-idempotency-value" not in str(event.metadata_json)
        legacy = await create_investigation(
            session,
            company_id=target,
            created_by=actor_id,
            as_of_date=date(2026, 9, 11),
        )
        legacy_id = legacy.id
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.investigation_id == legacy_id)
            )
            == 0
        )


async def fail_audit(*args: object, **kwargs: object) -> AuditEvent:
    del args, kwargs
    raise RuntimeError("forced audit failure")


async def test_audit_failure_rolls_back_creation_and_rerun(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor_id, analyst = await identity("analyst")
    target = await company()
    monkeypatch.setattr(investigation_service, "append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="forced audit failure"):
        await investigation_service.create_authenticated_investigation(
            app.state.session_factory,
            company_id=target,
            created_by=actor_id,
            as_of_date=date(2026, 9, 11),
            idempotency_key="rollback-create",
        )
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(Investigation)) == 0
        )
    monkeypatch.setattr(
        investigation_service, "append_audit_event", audit_service.append_audit_event
    )
    terminal = await create_terminal(actor_id)
    monkeypatch.setattr(investigation_service, "append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="forced audit failure"):
        await investigation_service.create_rerun(
            app.state.session_factory,
            investigation_id=terminal.id,
            actor_id=actor_id,
            idempotency_key="rollback-rerun",
        )
    async with app.state.session_factory() as session:
        persisted = await session.get(Investigation, terminal.id)
        assert (
            persisted is not None
            and persisted.current_run_id == terminal.current_run_id
        )
        assert await session.scalar(select(func.count()).select_from(RerunRequest)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkflowRun)
                .where(WorkflowRun.investigation_id == terminal.id)
            )
            == 1
        )
    monkeypatch.setattr(
        investigation_service, "append_audit_event", audit_service.append_audit_event
    )
    assert (
        await rerun(client, terminal.id, analyst, "successful-after-rollback")
    ).status_code == 200


async def test_audit_failure_rolls_back_review_report_and_worker(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    del client
    analyst_id, _ = await identity("analyst")
    reviewer_id, _ = await identity("reviewer")
    awaiting = await create_terminal(analyst_id, "awaiting_review")
    monkeypatch.setattr(review_service, "append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="forced audit failure"):
        await review_service.submit_review(
            app.state.session_factory,
            review_service.RunReview(
                run_id=awaiting.current_run_id,
                reviewer_id=reviewer_id,
                idempotency_key="rollback-review",
                decision="approve",
                reason="sensitive review reason",
            ),
        )
    async with app.state.session_factory() as session:
        run = await session.get(WorkflowRun, awaiting.current_run_id)
        assert run is not None and run.status == "awaiting_review"
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 0
        )

    report_investigation = await create_terminal(analyst_id, "running")
    async with app.state.session_factory.begin() as session:
        for source in ("business", "judicial", "news", "intellectual_property"):
            session.add(
                SourceCoverage(
                    company_id=report_investigation.company_id,
                    source_type=source,
                    as_of_date=date(2026, 9, 11),
                    status="available",
                    checked_at=datetime.now(UTC),
                )
            )
    monkeypatch.setattr(report_service, "append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="forced audit failure"):
        await report_service.generate_report(
            app.state.session_factory, report_investigation.current_run_id
        )
    async with app.state.session_factory() as session:
        run = await session.get(WorkflowRun, report_investigation.current_run_id)
        assert run is not None and run.status == "running"
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 0

    failed_investigation = await create_terminal(analyst_id, "running")
    monkeypatch.setattr(worker, "append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="forced audit failure"):
        await worker.mark_run_failed(
            app.state.session_factory, failed_investigation.current_run_id
        )
    async with app.state.session_factory() as session:
        run = await session.get(WorkflowRun, failed_investigation.current_run_id)
        assert run is not None and run.status == "running"


async def test_review_report_and_failure_audit_metadata_excludes_sensitive_text(
    client: httpx.AsyncClient,
) -> None:
    del client
    analyst_id, _ = await identity("analyst")
    reviewer_id, _ = await identity("reviewer")
    awaiting = await create_terminal(analyst_id, "awaiting_review")
    review_request = review_service.RunReview(
        run_id=awaiting.current_run_id,
        reviewer_id=reviewer_id,
        idempotency_key="private-review-key",
        decision="approve",
        reason="private review reason",
    )
    decision_id = await review_service.submit_review(
        app.state.session_factory, review_request
    )
    assert (
        await review_service.submit_review(app.state.session_factory, review_request)
        == decision_id
    )
    report_investigation = await create_terminal(analyst_id, "running")
    async with app.state.session_factory.begin() as session:
        for source in ("business", "judicial", "news", "intellectual_property"):
            session.add(
                SourceCoverage(
                    company_id=report_investigation.company_id,
                    source_type=source,
                    as_of_date=date(2026, 9, 11),
                    status="available",
                    checked_at=datetime.now(UTC),
                )
            )
    report_id = await report_service.generate_report(
        app.state.session_factory, report_investigation.current_run_id
    )
    assert (
        await report_service.generate_report(
            app.state.session_factory, report_investigation.current_run_id
        )
        == report_id
    )
    failure = await create_terminal(analyst_id, "running")
    assert await worker.mark_run_failed(
        app.state.session_factory, failure.current_run_id
    )
    assert not await worker.mark_run_failed(
        app.state.session_factory, failure.current_run_id
    )
    async with app.state.session_factory() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id.in_(
                        [decision_id, report_id, failure.current_run_id]
                    )
                )
            )
        ).all()
        assert len(events) == 3
        assert {event.event_type for event in events} == {
            "review.submitted",
            "report.generated",
            "workflow.failed",
        }
        serialized = str([event.metadata_json for event in events])
        for secret in (
            "private-review-key",
            "private review reason",
            "password",
            "authorization",
            "token",
            "evidence content",
            "summary",
            "reasoning",
        ):
            assert secret not in serialized.lower()
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action_key == f"run:{failure.current_run_id}:failed")
            )
            == 1
        )


@pytest.mark.parametrize("failure_kind", ["permanent", "retry_exhausted"])
async def test_real_worker_failure_branches_audit_once_on_redelivery(
    worker_resources: WorkerResources,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    sessions, worker_app = worker_resources
    actor_id, _ = await identity("analyst")
    investigation = await create_terminal(actor_id, "queued")

    async def fail_workflow(*args: object) -> None:
        del args
        if failure_kind == "permanent":
            raise PermanentWorkflowError("permanent model output")
        raise RuntimeError("retryable worker failure")

    monkeypatch.setattr(worker, "execute_workflow", fail_workflow)
    assert await dispatch_once(sessions, worker_app) == 1
    attempts = 1 if failure_kind == "permanent" else 3
    for _ in range(attempts):
        await consume(worker_app)
        if failure_kind == "retry_exhausted":
            await asyncio.sleep(1.05)
    action_key = f"run:{investigation.current_run_id}:failed"
    async with sessions() as session:
        run = await session.get(WorkflowRun, investigation.current_run_id)
        assert run is not None and run.status == "failed"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action_key == action_key,
                    AuditEvent.event_type == "workflow.failed",
                )
            )
            == 1
        )
    await enqueue_run(worker_app, investigation.current_run_id, "start")
    await consume(worker_app)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action_key == action_key,
                    AuditEvent.event_type == "workflow.failed",
                )
            )
            == 1
        )
