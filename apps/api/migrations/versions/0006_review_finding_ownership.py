"""Require finding-level reviews to belong to the finding's Run."""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_ai_finding_id_run", "ai_findings", ["id", "run_id"])
    op.create_foreign_key(
        "fk_review_decision_ai_finding_run",
        "review_decisions",
        "ai_findings",
        ["ai_finding_id", "run_id"],
        ["id", "run_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_review_decision_ai_finding", "review_decisions", type_="foreignkey"
    )


def downgrade() -> None:
    op.create_foreign_key(
        "fk_review_decision_ai_finding",
        "review_decisions",
        "ai_findings",
        ["ai_finding_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_review_decision_ai_finding_run", "review_decisions", type_="foreignkey"
    )
    op.drop_constraint("uq_ai_finding_id_run", "ai_findings", type_="unique")
