"""Add Stage 3 company aliases and source coverage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("alias_type", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "name", "alias_type", name="uq_company_alias_identity"
        ),
    )
    op.create_index(
        op.f("ix_company_aliases_company_id"),
        "company_aliases",
        ["company_id"],
        unique=False,
    )
    op.create_table(
        "source_coverage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_source_coverage_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('available', 'unavailable', 'stale')",
            name="ck_source_coverage_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "source_type",
            "as_of_date",
            name="uq_source_coverage_company_source_date",
        ),
    )
    op.create_index(
        op.f("ix_source_coverage_company_id"),
        "source_coverage",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_coverage_company_date",
        "source_coverage",
        ["company_id", "as_of_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_source_coverage_company_date", table_name="source_coverage")
    op.drop_index(op.f("ix_source_coverage_company_id"), table_name="source_coverage")
    op.drop_table("source_coverage")
    op.drop_index(op.f("ix_company_aliases_company_id"), table_name="company_aliases")
    op.drop_table("company_aliases")
