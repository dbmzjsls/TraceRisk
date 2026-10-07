from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint(
            "created_by",
            "creation_idempotency_key",
            name="uq_investigation_creation_request",
        ),
        ForeignKeyConstraint(
            ["current_run_id", "id"],
            ["workflow_runs.id", "workflow_runs.investigation_id"],
            name="fk_investigation_current_run",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Identity tables belong to stage 2; these are opaque spike inputs for now.
    company_id: Mapped[UUID]
    created_by: Mapped[UUID]
    creation_idempotency_key: Mapped[str | None] = mapped_column(Text)
    as_of_date: Mapped[date]
    current_run_id: Mapped[UUID] = mapped_column(index=True)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("id", "investigation_id", name="uq_run_investigation"),
        UniqueConstraint("investigation_id", "run_number", name="uq_run_number"),
        CheckConstraint("run_number > 0", name="ck_run_number"),
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_review', "
            "'resume_queued', 'completed', 'failed')",
            name="ck_run_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id", deferrable=True, initially="DEFERRED"),
        index=True,
    )
    run_number: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(Text, default="queued")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_workflow_event_sequence"),
        UniqueConstraint("run_id", "event_key", name="uq_workflow_event_key"),
        CheckConstraint("sequence > 0", name="ck_workflow_event_sequence"),
        CheckConstraint("length(btrim(event_key)) > 0", name="ck_workflow_event_key"),
        CheckConstraint(
            "event_type IN ('run.queued', 'run.running', 'node.completed', "
            "'run.awaiting_review', 'run.resume_queued', 'run.completed', "
            "'run.failed')",
            name="ck_workflow_event_type",
        ),
        CheckConstraint(
            "run_status IN ('queued', 'running', 'awaiting_review', "
            "'resume_queued', 'completed', 'failed')",
            name="ck_workflow_event_run_status",
        ),
        Index("ix_workflow_events_run_sequence", "run_id", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "workflow_runs.id",
            name="fk_workflow_event_run",
            ondelete="RESTRICT",
        )
    )
    sequence: Mapped[int]
    event_key: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    node_name: Mapped[str | None] = mapped_column(Text)
    run_status: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RerunRequest(Base):
    __tablename__ = "rerun_requests"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_rerun_actor_key"),
        UniqueConstraint("run_id", name="uq_rerun_created_run"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(Text)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    run_number: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DispatchOutbox(Base):
    __tablename__ = "dispatch_outbox"
    __table_args__ = (
        UniqueConstraint("run_id", "operation", name="uq_outbox_operation"),
        CheckConstraint("operation IN ('start', 'resume')", name="ck_outbox_operation"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    operation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_workflow_step"),
        CheckConstraint("attempt_count > 0", name="ck_step_attempt_count"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(default=1)
