from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class RuleFinding(Base):
    __tablename__ = "rule_findings"
    __table_args__ = (
        UniqueConstraint("run_id", "rule_code", name="uq_rule_finding_run_code"),
        CheckConstraint("score >= 0 AND score <= 95", name="ck_rule_finding_score"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), index=True
    )
    rule_code: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    score: Mapped[int]
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB)


class RuleFindingEvidence(Base):
    __tablename__ = "rule_finding_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evidence_id", "content_hash"],
            ["evidence.id", "evidence.content_hash"],
            name="fk_rule_finding_evidence_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rule_finding_evidence_content_hash",
        ),
        Index("ix_rule_finding_evidence_evidence_id", "evidence_id"),
    )

    rule_finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_findings.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(Text)
