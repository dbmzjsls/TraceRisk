from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.ai.provenance import load_model_execution


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("model", "prompt_version", "expected"),
    [("mock", "mock-v1", "mock"), ("mock", "risk-evidence-v2", "real")],
)
async def test_source_uses_saved_adapter_marker_not_model_name(
    model: str, prompt_version: str, expected: str
) -> None:
    attempts = [
        SimpleNamespace(
            model=model,
            prompt_version=prompt_version,
            status="succeeded",
            error_category=None,
        )
    ]

    class Session:
        async def scalars(self, query: Any) -> Any:
            return SimpleNamespace(all=lambda: attempts)

    result = await load_model_execution(cast(AsyncSession, Session()), uuid4())
    assert result.mode == expected
