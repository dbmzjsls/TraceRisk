"""spike_tables

Revision ID: 0001
Revises: 0000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = "0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("current_run_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_investigations_current_run_id"),
        "investigations",
        ["current_run_id"],
        unique=False,
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_review', "
            "'resume_queued', 'completed', 'failed')",
            name="ck_run_status",
        ),
        sa.CheckConstraint("run_number > 0", name="ck_run_number"),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "investigation_id", name="uq_run_investigation"),
        sa.UniqueConstraint("investigation_id", "run_number", name="uq_run_number"),
    )
    op.create_index(
        op.f("ix_workflow_runs_investigation_id"),
        "workflow_runs",
        ["investigation_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_investigation_current_run",
        "investigations",
        "workflow_runs",
        ["current_run_id", "id"],
        ["id", "investigation_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "dispatch_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation IN ('start', 'resume')", name="ck_outbox_operation"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "operation", name="uq_outbox_operation"),
    )
    op.create_index(
        op.f("ix_dispatch_outbox_run_id"), "dispatch_outbox", ["run_id"], unique=False
    )
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempt_count > 0", name="ck_step_attempt_count"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "name", name="uq_workflow_step"),
    )
    op.create_index(
        op.f("ix_workflow_steps_run_id"), "workflow_steps", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_investigation_current_run", "investigations", type_="foreignkey"
    )
    op.drop_index(op.f("ix_workflow_steps_run_id"), table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index(op.f("ix_dispatch_outbox_run_id"), table_name="dispatch_outbox")
    op.drop_table("dispatch_outbox")
    op.drop_index(op.f("ix_workflow_runs_investigation_id"), table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index(op.f("ix_investigations_current_run_id"), table_name="investigations")
    op.drop_table("investigations")
