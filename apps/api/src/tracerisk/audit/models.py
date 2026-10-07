from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('investigation.created', 'investigation.rerun_created', "
            "'workflow.failed', 'review.submitted', 'report.generated')",
            name="ck_audit_event_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(Text)
    action_key: Mapped[str] = mapped_column(Text, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[UUID]
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql")
    )
