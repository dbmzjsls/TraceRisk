"""Add the Stage 5 durable workflow event log."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("node_name", sa.Text(), nullable=True),
        sa.Column("run_status", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("sequence > 0", name="ck_workflow_event_sequence"),
        sa.CheckConstraint(
            "length(btrim(event_key)) > 0", name="ck_workflow_event_key"
        ),
        sa.CheckConstraint(
            "event_type IN ('run.queued', 'run.running', 'node.completed', "
            "'run.awaiting_review', 'run.resume_queued', 'run.completed', "
            "'run.failed')",
            name="ck_workflow_event_type",
        ),
        sa.CheckConstraint(
            "run_status IN ('queued', 'running', 'awaiting_review', "
            "'resume_queued', 'completed', 'failed')",
            name="ck_workflow_event_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_event_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_workflow_event_sequence"),
        sa.UniqueConstraint("run_id", "event_key", name="uq_workflow_event_key"),
    )
    op.create_index(
        "ix_workflow_events_run_sequence",
        "workflow_events",
        ["run_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_events_run_sequence", table_name="workflow_events")
    op.drop_table("workflow_events")
