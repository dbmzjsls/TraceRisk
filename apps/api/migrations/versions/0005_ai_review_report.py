"""Add Run-scoped AI findings, retrieval records, and report HTML."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("finding_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("claim_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_sufficiency", sa.Text(), nullable=False),
        sa.Column("confidence_level", sa.Text(), nullable=False),
        sa.Column("review_recommended", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "confidence_level IN ('low', 'medium', 'high')",
            name="ck_ai_finding_confidence_level",
        ),
        sa.CheckConstraint(
            "evidence_sufficiency IN ('sufficient', 'insufficient')",
            name="ck_ai_finding_evidence_sufficiency",
        ),
        sa.CheckConstraint(
            "risk_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_ai_finding_risk_type",
        ),
        sa.CheckConstraint("version > 0", name="ck_ai_finding_version"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
            name="fk_ai_finding_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "finding_key", name="uq_ai_finding_run_key"),
    )
    op.create_index("ix_ai_findings_run_id", "ai_findings", ["run_id"], unique=False)

    op.create_unique_constraint(
        "uq_evidence_id_company_hash",
        "evidence",
        ["id", "company_id", "content_hash"],
    )
    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.Date(), nullable=False),
        sa.Column("fact_type", sa.Text(), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("fact_value", sa.Text(), nullable=False),
        sa.Column("polarity", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_chunk_content_hash",
        ),
        sa.CheckConstraint(
            "polarity IN ('positive', 'negative', 'neutral')",
            name="ck_evidence_chunk_polarity",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "company_id", "content_hash"],
            ["evidence.id", "evidence.company_id", "evidence.content_hash"],
            name="fk_evidence_chunk_evidence_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evidence_chunks_company_id",
        "evidence_chunks",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_chunks_evidence_id",
        "evidence_chunks",
        ["evidence_id"],
        unique=False,
    )

    op.create_table(
        "retrieved_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "risk_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_retrieved_evidence_risk_type",
        ),
        sa.CheckConstraint("rank > 0", name="ck_retrieved_evidence_rank"),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["evidence_chunks.id"],
            name="fk_retrieved_evidence_chunk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
            name="fk_retrieved_evidence_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "risk_type",
            "chunk_id",
            name="uq_retrieved_evidence_run_risk_chunk",
        ),
    )
    op.create_index(
        "ix_retrieved_evidence_chunk_id",
        "retrieved_evidence",
        ["chunk_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieved_evidence_run_id",
        "retrieved_evidence",
        ["run_id"],
        unique=False,
    )

    op.create_index(
        "ix_review_decisions_ai_finding_id",
        "review_decisions",
        ["ai_finding_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_review_decision_ai_finding",
        "review_decisions",
        "ai_findings",
        ["ai_finding_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "risk_reports",
        sa.Column(
            "html_content", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
    )
    op.alter_column("risk_reports", "html_content", server_default=None)


def downgrade() -> None:
    op.drop_column("risk_reports", "html_content")

    op.drop_constraint(
        "fk_review_decision_ai_finding", "review_decisions", type_="foreignkey"
    )
    op.drop_index("ix_review_decisions_ai_finding_id", table_name="review_decisions")

    op.drop_index("ix_retrieved_evidence_run_id", table_name="retrieved_evidence")
    op.drop_index("ix_retrieved_evidence_chunk_id", table_name="retrieved_evidence")
    op.drop_table("retrieved_evidence")

    op.drop_index("ix_evidence_chunks_evidence_id", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_company_id", table_name="evidence_chunks")
    op.drop_table("evidence_chunks")
    op.drop_constraint("uq_evidence_id_company_hash", "evidence", type_="unique")

    op.drop_index("ix_ai_findings_run_id", table_name="ai_findings")
    op.drop_table("ai_findings")
