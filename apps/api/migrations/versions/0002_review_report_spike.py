"""review_report_spike

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ai_finding_id", sa.Uuid(), nullable=True),
        sa.Column("finding_version", sa.Integer(), nullable=True),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')", name="ck_review_decision"
        ),
        sa.CheckConstraint(
            "(ai_finding_id IS NULL) = (finding_version IS NULL)",
            name="ck_review_version",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reviewer_id", "idempotency_key", name="uq_review_request"),
        sa.UniqueConstraint("run_id", "ai_finding_id", name="uq_review_finding"),
    )
    op.create_index(
        "uq_review_run_level",
        "review_decisions",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("ai_finding_id IS NULL"),
    )
    op.create_table(
        "risk_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("risk_reports")
    op.drop_index(
        "uq_review_run_level",
        table_name="review_decisions",
        postgresql_where=sa.text("ai_finding_id IS NULL"),
    )
    op.drop_table("review_decisions")
