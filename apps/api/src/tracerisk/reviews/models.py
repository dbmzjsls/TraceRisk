from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "ai_finding_id", name="uq_review_finding"),
        UniqueConstraint("reviewer_id", "idempotency_key", name="uq_review_request"),
        UniqueConstraint(
            "id",
            "run_id",
            "ai_finding_id",
            "finding_version",
            "reviewer_id",
            name="uq_review_version_binding",
        ),
        ForeignKeyConstraint(
            ["ai_finding_id", "run_id"],
            ["ai_findings.id", "ai_findings.run_id"],
            name="fk_review_decision_ai_finding_run",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_review_run_level",
            "run_id",
            unique=True,
            postgresql_where=text("ai_finding_id IS NULL"),
        ),
        CheckConstraint(
            "decision IN ('approve', 'modify', 'reject')", name="ck_review_decision"
        ),
        CheckConstraint(
            "decision <> 'modify' OR ai_finding_id IS NOT NULL",
            name="ck_review_modify_finding",
        ),
        CheckConstraint(
            "(ai_finding_id IS NULL) = (finding_version IS NULL)",
            name="ck_review_version",
        ),
        Index("ix_review_decisions_ai_finding_id", "ai_finding_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"))
    ai_finding_id: Mapped[UUID | None]
    finding_version: Mapped[int | None]
    reviewer_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)


class HumanFindingVersion(Base):
    __tablename__ = "human_finding_versions"
    __table_args__ = (
        UniqueConstraint("review_decision_id", name="uq_human_finding_review"),
        UniqueConstraint("ai_finding_id", "version", name="uq_human_finding_version"),
        ForeignKeyConstraint(
            ["ai_finding_id", "run_id"],
            ["ai_findings.id", "ai_findings.run_id"],
            name="fk_human_finding_ai_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "review_decision_id",
                "run_id",
                "ai_finding_id",
                "finding_version",
                "reviewer_id",
            ],
            [
                "review_decisions.id",
                "review_decisions.run_id",
                "review_decisions.ai_finding_id",
                "review_decisions.finding_version",
                "review_decisions.reviewer_id",
            ],
            name="fk_human_finding_review_binding",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version > 0 AND finding_version > 0", name="ck_human_finding_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_decision_id: Mapped[UUID]
    run_id: Mapped[UUID]
    ai_finding_id: Mapped[UUID]
    finding_version: Mapped[int]
    reviewer_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(default=1)
    # Keep the submitted draft for exact replay; validator may downgrade support.
    submitted_replacement: Mapped[dict[str, object]] = mapped_column(JSONB)
    replacement: Mapped[dict[str, object]] = mapped_column(JSONB)
