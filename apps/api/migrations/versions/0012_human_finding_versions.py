"""Persist human replacements without changing immutable original AI findings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_review_decision", "review_decisions", type_="check")
    op.create_check_constraint(
        "ck_review_decision",
        "review_decisions",
        "decision IN ('approve', 'modify', 'reject')",
    )
    op.create_check_constraint(
        "ck_review_modify_finding",
        "review_decisions",
        "decision <> 'modify' OR ai_finding_id IS NOT NULL",
    )
    binding = ["id", "run_id", "ai_finding_id", "finding_version", "reviewer_id"]
    op.create_unique_constraint(
        "uq_review_version_binding", "review_decisions", binding
    )
    op.create_table(
        "human_finding_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_decision_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ai_finding_id", sa.Uuid(), nullable=False),
        sa.Column("finding_version", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("submitted_replacement", postgresql.JSONB(), nullable=False),
        sa.Column("replacement", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_decision_id", name="uq_human_finding_review"),
        sa.UniqueConstraint(
            "ai_finding_id", "version", name="uq_human_finding_version"
        ),
        sa.CheckConstraint(
            "version > 0 AND finding_version > 0", name="ck_human_finding_version"
        ),
        sa.ForeignKeyConstraint(
            ["ai_finding_id", "run_id"],
            ["ai_findings.id", "ai_findings.run_id"],
            name="fk_human_finding_ai_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_decision_id", *binding[1:]],
            [f"review_decisions.{column}" for column in binding],
            name="fk_human_finding_review_binding",
            ondelete="RESTRICT",
        ),
    )
    op.execute("""
        CREATE FUNCTION prevent_finding_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Persisted findings are immutable'
                USING ERRCODE = '23514';
        END $$
    """)
    for table in ("ai_findings", "human_finding_versions"):
        op.execute(f"""
            CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_finding_mutation()
        """)


def downgrade() -> None:
    # A rollback must not silently discard a saved human review.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM human_finding_versions)
                OR EXISTS (SELECT 1 FROM review_decisions WHERE decision = 'modify')
            THEN
                RAISE EXCEPTION 'Cannot downgrade while human review audit exists';
            END IF;
        END $$
    """)
    for table in ("human_finding_versions", "ai_findings"):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    op.execute("DROP FUNCTION prevent_finding_mutation()")
    op.drop_table("human_finding_versions")
    op.drop_constraint("uq_review_version_binding", "review_decisions", type_="unique")
    op.drop_constraint("ck_review_modify_finding", "review_decisions", type_="check")
    op.drop_constraint("ck_review_decision", "review_decisions", type_="check")
    op.create_check_constraint(
        "ck_review_decision", "review_decisions", "decision IN ('approve', 'reject')"
    )
