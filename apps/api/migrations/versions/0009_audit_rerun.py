"""Add append-only lifecycle audit and rerun replay records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rerun_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_rerun_actor_key"),
        sa.UniqueConstraint("run_id", name="uq_rerun_created_run"),
    )
    op.create_index(op.f("ix_rerun_requests_actor_id"), "rerun_requests", ["actor_id"])
    op.create_index(
        op.f("ix_rerun_requests_investigation_id"),
        "rerun_requests",
        ["investigation_id"],
    )
    op.create_index(op.f("ix_rerun_requests_run_id"), "rerun_requests", ["run_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("action_key", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('investigation.created', 'investigation.rerun_created', "
            "'workflow.failed', 'review.submitted', 'report.generated')",
            name="ck_audit_event_type",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_key"),
    )
    op.create_index(op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"])
    op.create_index(
        op.f("ix_audit_events_investigation_id"), "audit_events", ["investigation_id"]
    )
    op.create_index(op.f("ix_audit_events_run_id"), "audit_events", ["run_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_run_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_investigation_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(op.f("ix_rerun_requests_run_id"), table_name="rerun_requests")
    op.drop_index(
        op.f("ix_rerun_requests_investigation_id"), table_name="rerun_requests"
    )
    op.drop_index(op.f("ix_rerun_requests_actor_id"), table_name="rerun_requests")
    op.drop_table("rerun_requests")
