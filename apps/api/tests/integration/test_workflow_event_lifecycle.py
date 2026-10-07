from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.ai import service as ai
from tracerisk.ai.models import AIFinding
from tracerisk.audit.models import AuditEvent
from tracerisk.auth.models import User
from tracerisk.auth.seed import seed_accounts
from tracerisk.config import Settings
from tracerisk.evidence import service as evidence
from tracerisk.evidence.models import RetrievedEvidence
from tracerisk.investigations import service as creation
from tracerisk.investigations.models import (
    DispatchOutbox,
    Investigation,
    RerunRequest,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStep,
)
from tracerisk.reports import service as reports
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews import service as reviews
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.models import RuleFinding
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog
from tracerisk.workflows import spike_graph, worker
from tracerisk.workflows.events import append_workflow_event

pytestmark = pytest.mark.anyio
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def sessions(database: Settings) -> AsyncIterator[Sessions]:
    engine = create_async_engine(database.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await spike_graph.initialize_checkpoints()
        async with factory() as session:
            await seed_accounts(session)
        async with factory.begin() as session:
            await import_catalog(session, generate_catalog())
            await build_index(session, FakeEmbedding(), IndexContract.fake())
        yield factory
    finally:
        await engine.dispose()


async def new_run(
    sessions: Sessions, scenario: str = "clean-operations"
) -> Investigation:
    company = next(s.company.id for s in generate_catalog() if s.key == scenario)
    async with sessions() as session:
        owner = await session.scalar(select(User.id).where(User.username == "analyst"))
    assert owner is not None
    return await creation.create_authenticated_investigation(
        sessions,
        company_id=company,
        created_by=owner,
        as_of_date=date(2026, 9, 11),
        idempotency_key=scenario,
    )


async def events(sessions: Sessions, run_id: UUID) -> list[WorkflowEvent]:
    async with sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.run_id == run_id)
                    .order_by(WorkflowEvent.sequence)
                )
            ).all()
        )
    assert [r.sequence for r in rows] == list(range(1, len(rows) + 1))
    assert len({r.event_key for r in rows}) == len(rows)
    assert all(
        set(r.metadata_json) <= {"run_number", "attempt_count", "reason_codes"}
        for r in rows
    )
    return rows


async def test_creation_rerun_and_replay(sessions: Sessions) -> None:
    investigation = await new_run(sessions)
    assert (await new_run(sessions)).id == investigation.id
    first = await events(sessions, investigation.current_run_id)
    assert [(e.event_key, e.event_type, e.metadata_json) for e in first] == [
        ("run:queued", "run.queued", {"run_number": 1})
    ]
    await worker.mark_run_failed(sessions, investigation.current_run_id)
    rerun = await creation.create_rerun(
        sessions,
        investigation_id=investigation.id,
        actor_id=investigation.created_by,
        idempotency_key="rerun",
    )
    replay = await creation.create_rerun(
        sessions,
        investigation_id=investigation.id,
        actor_id=investigation.created_by,
        idempotency_key="rerun",
    )
    assert replay.id == rerun.id
    rows = await events(sessions, rerun.run_id)
    assert [(e.sequence, e.event_type, e.metadata_json) for e in rows] == [
        (1, "run.queued", {"run_number": 2})
    ]


async def review_requests(sessions: Sessions, run_id: UUID) -> list[reviews.RunReview]:
    async with sessions() as session:
        reviewer = await session.scalar(
            select(User.id).where(User.username == "reviewer")
        )
        findings = list(
            (
                await session.scalars(
                    select(AIFinding).where(AIFinding.run_id == run_id)
                )
            ).all()
        )
    assert reviewer is not None
    if not findings:
        return [
            reviews.RunReview(
                run_id=run_id,
                reviewer_id=reviewer,
                idempotency_key="zero",
                decision="approve",
                reason="Private human prose",
            )
        ]
    return [
        reviews.FindingReview(
            run_id=run_id,
            reviewer_id=reviewer,
            idempotency_key=str(f.id),
            decision="approve",
            reason="Private human prose",
            ai_finding_id=f.id,
            finding_version=f.version,
        )
        for f in findings
    ]


@pytest.mark.parametrize(
    "scenario", ["clean-operations", "unavailable-source", "all-rules-95"]
)
async def test_graph_lifecycle_replay(
    database: Settings, sessions: Sessions, scenario: str
) -> None:
    investigation = await new_run(sessions, scenario)
    run_id = investigation.current_run_id
    await worker.execute_probe(sessions, run_id)
    await worker.execute_probe(sessions, run_id)
    await spike_graph.execute_workflow(database, sessions, run_id, "start")
    expected = ["run:queued", "run:running:start"] + [
        f"node:{node}:completed"
        for node in (
            "prepare_context",
            "retrieve_evidence",
            "apply_rules",
            "generate_validate_findings",
            "route_result",
        )
    ]
    if scenario != "clean-operations":
        expected.append("run:awaiting_review")
        waiting = await events(sessions, run_id)
        assert [e.event_key for e in waiting] == expected
        assert waiting[-1].node_name == "review_interrupt"
        assert waiting[-1].metadata_json["reason_codes"]
        # The visible waiting event must have a persisted interrupt checkpoint.
        conninfo = database.database_url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
            config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
            state = await spike_graph.build_graph(saver, sessions).aget_state(config)
            assert any(task.interrupts for task in state.tasks)
        await spike_graph.execute_workflow(database, sessions, run_id, "start")
        requests = await review_requests(sessions, run_id)
        for index, request in enumerate(requests):
            decision = await reviews.submit_review(sessions, request)
            assert await reviews.submit_review(sessions, request) == decision
            if index < len(requests) - 1:
                assert [e.event_key for e in await events(sessions, run_id)] == expected
        expected += [
            "run:resume_queued",
            "run:running:resume",
            "node:review_interrupt:completed",
        ]
        await spike_graph.execute_workflow(database, sessions, run_id, "resume")
        await spike_graph.execute_workflow(database, sessions, run_id, "resume")
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DispatchOutbox)
                    .where(
                        DispatchOutbox.run_id == run_id,
                        DispatchOutbox.operation == "resume",
                    )
                )
                == 1
            )
    expected += ["node:generate_report:completed", "run:completed"]
    rows = await events(sessions, run_id)
    assert [e.event_key for e in rows] == expected
    assert rows[-2].run_status == rows[-1].run_status == "completed"
    await reports.generate_report(sessions, run_id)
    await spike_graph.execute_workflow(database, sessions, run_id, "start")
    assert [e.id for e in await events(sessions, run_id)] == [e.id for e in rows]


async def fail_event(*args: object, **kwargs: object) -> None:
    raise RuntimeError("injected event failure")


@pytest.mark.parametrize("operation", ["create", "rerun", "probe", "failed", "review"])
async def test_transition_event_failure_rolls_back(
    database: Settings,
    sessions: Sessions,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    investigation = await new_run(sessions, "unavailable-source")
    run_id = investigation.current_run_id
    if operation == "rerun":
        await worker.mark_run_failed(sessions, run_id)
    if operation == "review":
        await spike_graph.execute_workflow(database, sessions, run_id, "start")
    tables = (
        Investigation,
        WorkflowRun,
        DispatchOutbox,
        RerunRequest,
        AuditEvent,
        WorkflowEvent,
        WorkflowStep,
        ReviewDecision,
    )
    async with sessions() as session:
        before = [
            await session.scalar(select(func.count()).select_from(t)) for t in tables
        ]
        status = await session.scalar(
            select(WorkflowRun.status).where(WorkflowRun.id == run_id)
        )
    module = (
        creation
        if operation in {"create", "rerun"}
        else reviews
        if operation == "review"
        else worker
    )
    monkeypatch.setattr(module, "append_workflow_event", fail_event, raising=False)
    with pytest.raises(RuntimeError, match="injected event failure"):
        if operation == "create":
            await new_run(sessions)
        elif operation == "rerun":
            await creation.create_rerun(
                sessions,
                investigation_id=investigation.id,
                actor_id=investigation.created_by,
                idempotency_key="rollback",
            )
        elif operation == "probe":
            await worker.execute_probe(sessions, run_id)
        elif operation == "failed":
            await worker.mark_run_failed(sessions, run_id)
        else:
            await reviews.submit_review(
                sessions, (await review_requests(sessions, run_id))[0]
            )
    async with sessions() as session:
        assert [
            await session.scalar(select(func.count()).select_from(t)) for t in tables
        ] == before
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == status
        )


async def test_failure_event_is_atomic_and_idempotent(sessions: Sessions) -> None:
    run_id = (await new_run(sessions)).current_run_id
    assert await worker.mark_run_failed(sessions, run_id)
    assert not await worker.mark_run_failed(sessions, run_id)
    assert [e.event_key for e in await events(sessions, run_id)] == [
        "run:queued",
        "run:failed",
    ]
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.run_id == run_id,
                    AuditEvent.event_type == "workflow.failed",
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "failed_key", ["node:generate_report:completed", "run:completed"]
)
async def test_report_event_failure_rolls_back_and_replays(
    database: Settings,
    sessions: Sessions,
    monkeypatch: pytest.MonkeyPatch,
    failed_key: str,
) -> None:
    run_id = (await new_run(sessions)).current_run_id

    async def fail_selected(session: AsyncSession, **kwargs: object) -> WorkflowEvent:
        if kwargs["event_key"] == failed_key:
            raise RuntimeError("injected event failure")
        return await append_workflow_event(session, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as patch:
        patch.setattr(reports, "append_workflow_event", fail_selected, raising=False)
        with pytest.raises(RuntimeError, match="injected event failure"):
            await spike_graph.execute_workflow(database, sessions, run_id, "start")
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "running"
        )
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "report.generated")
            )
            == 0
        )
    assert (await events(sessions, run_id))[
        -1
    ].event_key == "node:route_result:completed"
    await spike_graph.execute_workflow(database, sessions, run_id, "start")
    assert [e.event_key for e in await events(sessions, run_id)][-2:] == [
        "node:generate_report:completed",
        "run:completed",
    ]


@pytest.mark.parametrize(
    "failed_key",
    [
        "run:running:start",
        "node:prepare_context:completed",
        "node:retrieve_evidence:completed",
        "node:apply_rules:completed",
        "node:generate_validate_findings:completed",
        "node:route_result:completed",
        "run:awaiting_review",
        "run:running:resume",
        "node:review_interrupt:completed",
    ],
)
async def test_node_and_checkpoint_failure_recovery(
    database: Settings,
    sessions: Sessions,
    monkeypatch: pytest.MonkeyPatch,
    failed_key: str,
) -> None:
    run_id = (await new_run(sessions, "all-rules-95")).current_run_id
    operation = "start"
    if failed_key in {"run:running:resume", "node:review_interrupt:completed"}:
        await spike_graph.execute_workflow(database, sessions, run_id, "start")
        for request in await review_requests(sessions, run_id):
            await reviews.submit_review(sessions, request)
        operation = "resume"

    async def fail_selected(session: AsyncSession, **kwargs: object) -> WorkflowEvent:
        row = await append_workflow_event(session, **kwargs)  # type: ignore[arg-type]
        if kwargs["event_key"] == failed_key:
            raise RuntimeError("injected event failure")
        return row

    with monkeypatch.context() as patch:
        for module in (spike_graph, evidence, ai):
            patch.setattr(module, "append_workflow_event", fail_selected, raising=False)
        with pytest.raises(RuntimeError, match="injected event failure"):
            await spike_graph.execute_workflow(database, sessions, run_id, operation)
    assert failed_key not in [e.event_key for e in await events(sessions, run_id)]
    async with sessions() as session:
        expected_status = (
            "queued"
            if failed_key == "run:running:start"
            else "resume_queued"
            if failed_key == "run:running:resume"
            else "running"
        )
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == expected_status
        )
        for key, model, step in (
            (
                "node:retrieve_evidence:completed",
                RetrievedEvidence,
                "retrieve_evidence",
            ),
            ("node:apply_rules:completed", RuleFinding, "apply_rules"),
            (
                "node:generate_validate_findings:completed",
                AIFinding,
                "generate_validate_findings",
            ),
        ):
            if failed_key == key:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.run_id == run_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(WorkflowStep.id).where(
                            WorkflowStep.run_id == run_id, WorkflowStep.name == step
                        )
                    )
                    is None
                )
    if failed_key == "run:awaiting_review":
        conninfo = database.database_url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
            config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
            graph = spike_graph.build_graph(saver, sessions)
            state = await graph.aget_state(config)
            assert any(task.interrupts for task in state.tasks)
            before = [e.id for e in await events(sessions, run_id)]
            # Re-execute committed nodes before publishing the waiting state.
            await graph.ainvoke({"run_id": str(run_id)}, config, durability="sync")
            assert [e.id for e in await events(sessions, run_id)] == before
    await spike_graph.execute_workflow(database, sessions, run_id, operation)
    rows = await events(sessions, run_id)
    assert sum(e.event_key == failed_key for e in rows) == 1
    assert rows[-1].event_key == (
        "run:completed" if operation == "resume" else "run:awaiting_review"
    )
