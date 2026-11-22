from typing import Literal

FailureCode = Literal[
    "workflow_error",
    "model_generation_failed",
    "citation_validation_failed",
    "retrieval_integrity_failed",
    "structured_output_failed",
]


class PermanentWorkflowError(ValueError):
    """Terminal workflow failures with a safe public category, never provider text."""

    def __init__(self, message: str, *, code: FailureCode = "workflow_error") -> None:
        super().__init__(message)
        self.code = code
