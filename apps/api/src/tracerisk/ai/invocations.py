from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.models import ModelInvocation
from tracerisk.ai.protocol import ModelAdapterError


@dataclass(frozen=True)
class InvocationAttempt:
    run_id: UUID
    logical_call_id: UUID
    attempt_number: int
    model: str
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    status: str
    error_category: str | None


InvocationRecorder = Callable[[InvocationAttempt], Awaitable[None]]


class DatabaseInvocationRecorder:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def __call__(self, attempt: InvocationAttempt) -> None:
        try:
            async with self.sessions.begin() as session:
                session.add(ModelInvocation(**asdict(attempt)))
        except Exception:
            raise ModelAdapterError("Model invocation recorder failed") from None
