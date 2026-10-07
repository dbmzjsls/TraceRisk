from typing import Protocol, runtime_checkable

from tracerisk.ai.schemas import RiskContext, StructuredRiskFindings


class ModelAdapterError(RuntimeError):
    """A terminal, secret-safe failure after the adapter's bounded attempts."""


@runtime_checkable
class ModelAdapter(Protocol):
    async def analyze_risk(self, context: RiskContext) -> StructuredRiskFindings: ...
