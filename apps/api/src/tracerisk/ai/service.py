"""Read frozen inputs, release locks for generation, then validate and persist."""

from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.invocations import DatabaseInvocationRecorder
from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.models import AIFinding
from tracerisk.ai.openai_compatible import OpenAICompatibleAdapter
from tracerisk.ai.protocol import ModelAdapter, ModelAdapterError
from tracerisk.ai.schemas import ValidatedRiskFindings
from tracerisk.ai.validation import CitationValidationError, validate_findings
from tracerisk.config import Settings
from tracerisk.evidence.service import (
    RETRIEVAL_MANIFEST_PREFIX,
    RetrievalIntegrityError,
    build_risk_context,
    current_investigation,
    step_completed,
)
from tracerisk.investigations.models import WorkflowStep
from tracerisk.workflows.errors import PermanentWorkflowError as PermanentWorkflowError
from tracerisk.workflows.events import append_workflow_event


async def _fingerprint(session: AsyncSession, run_id: UUID) -> str:
    manifests = (
        await session.scalars(
            select(WorkflowStep.name).where(
                WorkflowStep.run_id == run_id,
                WorkflowStep.name.startswith(RETRIEVAL_MANIFEST_PREFIX),
            )
        )
    ).all()
    if len(manifests) != 1:
        raise RetrievalIntegrityError("Missing or ambiguous retrieval manifest")
    return manifests[0]


async def generate_validate_findings(
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    settings: Settings | None = None,
    adapter: ModelAdapter | None = None,
) -> None:
    try:
        async with sessions.begin() as session:
            await current_investigation(session, run_id)
            if await step_completed(session, run_id, "generate_validate_findings"):
                return
            context = await build_risk_context(session, run_id=run_id)
            fingerprint = await _fingerprint(session, run_id)
        # No business transaction or row lock spans either model IO or its recorder.
        if adapter is not None:
            drafts = await adapter.analyze_risk(context)
        else:
            configuration = settings or Settings()
            if configuration.model_adapter == "mock":
                drafts = await MockModelAdapter().analyze_risk(context)
            else:
                assert configuration.model_base_url is not None
                assert configuration.model_name is not None
                assert configuration.model_api_key is not None
                async with httpx.AsyncClient(trust_env=False) as client:
                    drafts = await OpenAICompatibleAdapter(
                        client,
                        base_url=configuration.model_base_url,
                        model=configuration.model_name,
                        api_key=configuration.model_api_key,
                        run_id=run_id,
                        recorder=DatabaseInvocationRecorder(sessions),
                    ).analyze_risk(context)
        async with sessions.begin() as session:
            investigation = await current_investigation(session, run_id)
            reloaded = await build_risk_context(session, run_id=run_id)
            if (
                await _fingerprint(session, run_id) != fingerprint
                or reloaded != context
            ):
                raise PermanentWorkflowError(
                    "Frozen retrieval changed during generation"
                )
            if await step_completed(session, run_id, "generate_validate_findings"):
                return
            validated = await validate_findings(
                session,
                run_id=run_id,
                user_id=investigation.created_by,
                findings=drafts,
            )
            await _persist_findings(session, run_id=run_id, validated=validated)
    except (
        ModelAdapterError,
        CitationValidationError,
        RetrievalIntegrityError,
        ValidationError,
    ):
        raise PermanentWorkflowError("Model generation or validation failed") from None


async def _persist_findings(
    session: AsyncSession, *, run_id: UUID, validated: ValidatedRiskFindings
) -> None:
    expected_keys: set[str] = set()
    for draft in validated.findings:
        values = draft.model_dump(mode="json")
        values["citations"] = values.pop("evidence_citations")
        if draft.finding_key in expected_keys:
            raise PermanentWorkflowError("Duplicate model finding key")
        expected_keys.add(draft.finding_key)
        await session.execute(
            insert(AIFinding)
            .values(run_id=run_id, version=1, **values)
            .on_conflict_do_nothing(index_elements=["run_id", "finding_key"])
        )
        saved = await session.scalar(
            select(AIFinding).where(
                AIFinding.run_id == run_id, AIFinding.finding_key == draft.finding_key
            )
        )
        if (
            saved is None
            or saved.version != 1
            or any(getattr(saved, name) != value for name, value in values.items())
        ):
            raise PermanentWorkflowError(
                "Existing AI finding differs from validated output"
            )
    saved_keys = set(
        (
            await session.scalars(
                select(AIFinding.finding_key).where(AIFinding.run_id == run_id)
            )
        ).all()
    )
    if saved_keys != expected_keys:
        raise PermanentWorkflowError(
            "Existing AI finding set differs from validated output"
        )
    session.add(WorkflowStep(run_id=run_id, name="generate_validate_findings"))
    await session.flush()
    await append_workflow_event(
        session,
        run_id=run_id,
        event_key="node:generate_validate_findings:completed",
        event_type="node.completed",
        node_name="generate_validate_findings",
        run_status="running",
    )
