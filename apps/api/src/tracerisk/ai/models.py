from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class AIFinding(Base):
    __tablename__ = "ai_findings"
    __table_args__ = (
        UniqueConstraint("run_id", "finding_key", name="uq_ai_finding_run_key"),
        UniqueConstraint("id", "run_id", name="uq_ai_finding_id_run"),
        CheckConstraint("version > 0", name="ck_ai_finding_version"),
        CheckConstraint(
            "risk_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_ai_finding_risk_type",
        ),
        CheckConstraint(
            "evidence_sufficiency IN ('sufficient', 'insufficient')",
            name="ck_ai_finding_evidence_sufficiency",
        ),
        CheckConstraint(
            "confidence_level IN ('low', 'medium', 'high')",
            name="ck_ai_finding_confidence_level",
        ),
        Index("ix_ai_findings_run_id", "run_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_runs.id", name="fk_ai_finding_run", ondelete="RESTRICT")
    )
    finding_key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1)
    risk_type: Mapped[str] = mapped_column(Text)
    claim_code: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    evidence_sufficiency: Mapped[str] = mapped_column(Text)
    confidence_level: Mapped[str] = mapped_column(Text)
    review_recommended: Mapped[bool] = mapped_column(Boolean)
    # New findings receive both values only from the shared support validator.
    # Defaults preserve the interpretation of existing Stage 3 findings.
    support_status: Mapped[str] = mapped_column(
        Text, default="indeterminate", server_default="indeterminate"
    )
    support_reason: Mapped[str] = mapped_column(
        Text, default="legacy-stage3", server_default="legacy-stage3"
    )


class ModelInvocation(Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        UniqueConstraint(
            "logical_call_id", "attempt_number", name="uq_model_invocation_attempt"
        ),
        CheckConstraint("attempt_number IN (1, 2)", name="ck_model_invocation_attempt"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0",
            name="ck_model_invocation_metrics",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), index=True
    )
    logical_call_id: Mapped[UUID]
    attempt_number: Mapped[int]
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    latency_ms: Mapped[int]
    status: Mapped[str] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(Text)
