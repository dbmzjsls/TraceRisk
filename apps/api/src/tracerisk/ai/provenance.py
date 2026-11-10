from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.ai.models import ModelInvocation
from tracerisk.investigations.schemas import ModelExecutionResponse


async def load_model_execution(
    session: AsyncSession, run_id: UUID
) -> ModelExecutionResponse:
    """Use persisted attempts, never today's environment to label an old Run."""
    attempts = (
        await session.scalars(
            select(ModelInvocation).where(ModelInvocation.run_id == run_id)
        )
    ).all()
    models = sorted({attempt.model for attempt in attempts})
    simulated = bool(attempts) and all(
        attempt.model == "mock" and attempt.prompt_version == "mock-v1"
        for attempt in attempts
    )
    return ModelExecutionResponse(
        mode="unknown" if not models else "mock" if simulated else "real",
        models=models,
        statuses=sorted({attempt.status for attempt in attempts}),
        error_categories=sorted(
            {attempt.error_category for attempt in attempts if attempt.error_category}
        ),
    )
