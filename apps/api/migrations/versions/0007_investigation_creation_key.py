"""Persist authenticated investigation creation keys while preserving legacy rows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("investigations", sa.Column("creation_idempotency_key", sa.Text()))
    op.create_unique_constraint(
        "uq_investigation_creation_request",
        "investigations",
        ["created_by", "creation_idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_investigation_creation_request", "investigations", type_="unique"
    )
    op.drop_column("investigations", "creation_idempotency_key")
