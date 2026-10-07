import asyncio
import hashlib
import sys
from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.models import AIFinding
from tracerisk.ai.service import generate_validate_findings
from tracerisk.config import Settings
from tracerisk.evidence.conflicts import load_retrieved_conflicts
from tracerisk.evidence.coverage import load_coverage
from tracerisk.evidence.service import (
    RetrievalIntegrityError,
    current_investigation,
    retrieve_evidence,
    step_completed,
)
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.reports.service import generate_report
from tracerisk.retrieval.indexing import IndexContract, IndexReadinessError
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.engine import evaluate_all_rules_from_facts, save_rule_finding
from tracerisk.rules.models import RuleFinding
from tracerisk.workflows.errors import PermanentWorkflowError
from tracerisk.workflows.events import append_workflow_event
from tracerisk.workflows.routing import review_reasons


class SpikeState(TypedDict, total=False):
    run_id: str
    review_required: bool
    review_reason: list[str]
    review_id: str
    report_id: str


def build_graph(
    saver: AsyncPostgresSaver,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> CompiledStateGraph[SpikeState, None, SpikeState, SpikeState]:
    async def prepare_context(state: SpikeState) -> SpikeState:
        async with sessions.begin() as session:
            await current_investigation(session, UUID(state["run_id"]))
            await append_workflow_event(
                session,
                run_id=UUID(state["run_id"]),
                event_key="node:prepare_context:completed",
                event_type="node.completed",
                node_name="prepare_context",
                run_status="running",
            )
        return {}

    async def apply_rules(state: SpikeState) -> SpikeState:
        run_id = UUID(state["run_id"])
        async with sessions.begin() as session:
            investigation = await current_investigation(session, run_id)
            conflicts = await load_retrieved_conflicts(
                session,
                run_id=run_id,
                as_of_date=investigation.as_of_date,
            )
            score = evaluate_all_rules_from_facts(
                conflicts.resolved_facts,
                as_of_date=investigation.as_of_date,
            )
            completed = await step_completed(session, run_id, "apply_rules")
            expected_codes = {
                evaluation.rule_code
                for evaluation in score.evaluations
                if evaluation.is_match
            }
            if completed:
                saved_codes = set(
                    (
                        await session.scalars(
                            select(RuleFinding.rule_code).where(
                                RuleFinding.run_id == run_id
                            )
                        )
                    ).all()
                )
                if saved_codes != expected_codes:
                    raise ValueError(
                        "Existing rule finding set differs from evaluation"
                    )
            for evaluation in score.evaluations:
                await save_rule_finding(session, run_id=run_id, evaluation=evaluation)
            saved_codes = set(
                (
                    await session.scalars(
                        select(RuleFinding.rule_code).where(
                            RuleFinding.run_id == run_id
                        )
                    )
                ).all()
            )
            if saved_codes != expected_codes:
                raise ValueError("Existing rule finding set differs from evaluation")
            if not completed:
                session.add(WorkflowStep(run_id=run_id, name="apply_rules"))
            await append_workflow_event(
                session,
                run_id=run_id,
                event_key="node:apply_rules:completed",
                event_type="node.completed",
                node_name="apply_rules",
                run_status="running",
            )
        return {}

    async def retrieve(state: SpikeState) -> SpikeState:
        async with sessions.begin() as session:
            await retrieve_evidence(
                session,
                run_id=UUID(state["run_id"]),
                contract=IndexContract.from_settings(settings or Settings()),
            )
        return {}

    async def generate(state: SpikeState) -> SpikeState:
        await generate_validate_findings(
            sessions, run_id=UUID(state["run_id"]), settings=settings
        )
        return {}

    async def route_result(state: SpikeState) -> SpikeState:
        run_id = UUID(state["run_id"])
        async with sessions.begin() as session:
            investigation = await current_investigation(session, run_id)
            scores = (
                await session.scalars(
                    select(RuleFinding.score).where(RuleFinding.run_id == run_id)
                )
            ).all()
            findings = (
                await session.scalars(
                    select(AIFinding).where(AIFinding.run_id == run_id)
                )
            ).all()
            coverage = await load_coverage(
                session,
                company_id=investigation.company_id,
                as_of_date=investigation.as_of_date,
            )
            conflicts = await load_retrieved_conflicts(
                session,
                run_id=run_id,
                as_of_date=investigation.as_of_date,
            )
            reasons = review_reasons(
                rule_score=sum(scores),
                evidence_sufficient=(
                    coverage.is_sufficient and not conflicts.has_unresolved
                ),
                conflict=conflicts.requires_review,
                model_evidence_insufficient=any(
                    f.evidence_sufficiency == "insufficient" for f in findings
                ),
                model_recommended=any(f.review_recommended for f in findings),
                support_reasons=(f.support_reason for f in findings),
            )
            await append_workflow_event(
                session,
                run_id=run_id,
                event_key="node:route_result:completed",
                event_type="node.completed",
                node_name="route_result",
                run_status="running",
            )
        return {"review_required": bool(reasons), "review_reason": reasons}

    async def review_interrupt(state: SpikeState) -> SpikeState:
        # This node can be replayed: no non-idempotent write precedes interrupt.
        review_id = interrupt(
            {"run_id": state["run_id"], "reason": state["review_reason"]}
        )
        async with sessions.begin() as session:
            await current_investigation(session, UUID(state["run_id"]))
            await append_workflow_event(
                session,
                run_id=UUID(state["run_id"]),
                event_key="node:review_interrupt:completed",
                event_type="node.completed",
                node_name="review_interrupt",
                run_status="running",
            )
        return {"review_id": str(review_id)}

    async def report(state: SpikeState) -> SpikeState:
        report_id = await generate_report(
            sessions,
            UUID(state["run_id"]),
            UUID(state["review_id"]) if state.get("review_id") else None,
        )
        return {"report_id": str(report_id)}

    graph = StateGraph(SpikeState)
    graph.add_node("prepare_context", prepare_context)
    graph.add_node("apply_rules", apply_rules)
    graph.add_node("retrieve_evidence", retrieve)
    graph.add_node("generate_validate_findings", generate)
    graph.add_node("route_result", route_result)
    graph.add_node("review_interrupt", review_interrupt)
    graph.add_node("generate_report", report)
    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "retrieve_evidence")
    graph.add_edge("retrieve_evidence", "apply_rules")
    graph.add_edge("apply_rules", "generate_validate_findings")
    graph.add_edge("generate_validate_findings", "route_result")
    graph.add_conditional_edges(
        "route_result",
        lambda state: (
            "review_interrupt" if state["review_required"] else "generate_report"
        ),
    )
    graph.add_edge("review_interrupt", "generate_report")
    graph.add_edge("generate_report", END)
    return graph.compile(checkpointer=saver)


async def publish_waiting(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
    reason_codes: list[str] | None = None,
) -> None:
    async with sessions.begin() as session:
        changed = await session.scalar(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.status == "running",
                WorkflowRun.id.in_(select(Investigation.current_run_id)),
            )
            .values(status="awaiting_review")
            .returning(WorkflowRun.id)
        )
        if changed is None:
            raise ValueError("Run changed before publishing review interruption")
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="run:awaiting_review",
            event_type="run.awaiting_review",
            node_name="review_interrupt",
            run_status="awaiting_review",
            metadata={"reason_codes": reason_codes or []},
        )


async def execute_workflow(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
    operation: str,
) -> None:
    if operation not in {"start", "resume"}:
        raise ValueError("Unknown workflow operation")
    conninfo = settings.database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    async with await AsyncConnection.connect(
        conninfo, autocommit=True, row_factory=dict_row
    ) as connection:
        # Session advisory lock serializes all graph operations for this Run.
        # It is released by PostgreSQL when a crashed worker connection closes.
        lock_id = int.from_bytes(
            hashlib.blake2b(run_id.bytes, digest_size=8).digest(), "big", signed=True
        )
        await connection.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        try:
            async with sessions.begin() as session:
                run = await session.get(WorkflowRun, run_id, with_for_update=True)
                if run is None:
                    return
                current = await session.scalar(
                    select(Investigation.current_run_id).where(
                        Investigation.id == run.investigation_id
                    )
                )
                if current != run_id or run.status in {"completed", "failed"}:
                    return
                reviews = (
                    await session.scalars(
                        select(ReviewDecision).where(ReviewDecision.run_id == run_id)
                    )
                ).all()
                review_id = reviews[0].id if reviews else None
                if operation == "resume" and (
                    run.status not in {"resume_queued", "running"} or review_id is None
                ):
                    return
                if operation == "resume":
                    findings = (
                        await session.scalars(
                            select(AIFinding).where(AIFinding.run_id == run_id)
                        )
                    ).all()
                    if findings:
                        if any(
                            not any(
                                d.ai_finding_id == f.id
                                and d.finding_version == f.version
                                for d in reviews
                            )
                            for f in findings
                        ):
                            raise ValueError(
                                "Every finding requires a current-version review"
                            )
                    elif not any(
                        d.ai_finding_id is None and d.finding_version is None
                        for d in reviews
                    ):
                        raise ValueError("Zero findings require a run-level review")
                if operation == "start":
                    if run.status not in {"queued", "running"} or review_id is not None:
                        return
                elif (
                    run.status not in {"resume_queued", "running"} or review_id is None
                ):
                    return
                if run.status != "running":
                    changed = await session.scalar(
                        update(WorkflowRun)
                        .where(
                            WorkflowRun.id == run_id, WorkflowRun.status == run.status
                        )
                        .values(status="running")
                        .returning(WorkflowRun.id)
                    )
                    if changed is None:
                        raise ValueError("Run claim conflict")
                    await append_workflow_event(
                        session,
                        run_id=run_id,
                        event_key=f"run:running:{operation}",
                        event_type="run.running",
                        run_status="running",
                    )

            graph = build_graph(AsyncPostgresSaver(connection), sessions, settings)
            config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
            state = await graph.aget_state(config)
            interrupted = any(task.interrupts for task in state.tasks)
            if operation == "start":
                if not interrupted:
                    await graph.ainvoke(
                        None if state.values else {"run_id": str(run_id)},
                        config,
                        durability="sync",
                    )
                # Only publish awaiting_review after the checkpoint is durable.
                state = await graph.aget_state(config)
                if any(task.interrupts for task in state.tasks):
                    await publish_waiting(
                        sessions, run_id, state.values["review_reason"]
                    )
                else:
                    async with sessions() as session:
                        if (
                            await session.scalar(
                                select(WorkflowRun.status).where(
                                    WorkflowRun.id == run_id
                                )
                            )
                            != "completed"
                        ):
                            raise ValueError(
                                "Expected completed report or review interruption"
                            )
            else:
                if not state.values:
                    raise ValueError("Resume checkpoint is missing")
                await graph.ainvoke(
                    Command(resume=str(review_id)) if interrupted else None,
                    config,
                    durability="sync",
                )
        except (IndexReadinessError, RetrievalIntegrityError) as exc:
            raise PermanentWorkflowError(str(exc)) from exc
        finally:
            await connection.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))


async def initialize_checkpoints() -> None:
    conninfo = (
        Settings()
        .database_url.set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(initialize_checkpoints())
