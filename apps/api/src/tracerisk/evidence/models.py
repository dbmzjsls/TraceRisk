from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_evidence_source_type",
        ),
        CheckConstraint(
            "risk_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_evidence_risk_type",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_evidence_content_hash"
        ),
        UniqueConstraint("id", "content_hash", name="uq_evidence_id_content_hash"),
        UniqueConstraint(
            "id",
            "company_id",
            "content_hash",
            name="uq_evidence_id_company_hash",
        ),
        UniqueConstraint("company_id", "source_uri", name="uq_evidence_source_uri"),
        Index(
            "ix_evidence_company_risk_date", "company_id", "risk_type", "observed_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(Text)
    risk_type: Mapped[str] = mapped_column(Text)
    source_uri: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[date]
    fact_type: Mapped[str] = mapped_column(Text)
    fact_key: Mapped[str] = mapped_column(Text)
    fact_value: Mapped[str] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)


class SourceCoverage(Base):
    __tablename__ = "source_coverage"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_source_coverage_source_type",
        ),
        CheckConstraint(
            "status IN ('available', 'unavailable', 'stale')",
            name="ck_source_coverage_status",
        ),
        UniqueConstraint(
            "company_id",
            "source_type",
            "as_of_date",
            name="uq_source_coverage_company_source_date",
        ),
        Index("ix_source_coverage_company_date", "company_id", "as_of_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(Text)
    as_of_date: Mapped[date]
    status: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evidence_id", "company_id", "evidence_content_hash"],
            ["evidence.id", "evidence.company_id", "evidence.content_hash"],
            name="fk_evidence_chunk_evidence_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_chunk_content_hash",
        ),
        CheckConstraint(
            "polarity IN ('positive', 'negative', 'neutral')",
            name="ck_evidence_chunk_polarity",
        ),
        Index("ix_evidence_chunks_evidence_id", "evidence_id"),
        Index("ix_evidence_chunks_company_id", "company_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    evidence_id: Mapped[UUID]
    company_id: Mapped[UUID]
    source_type: Mapped[str] = mapped_column(Text)
    risk_type: Mapped[str] = mapped_column(Text)
    evidence_content_hash: Mapped[str] = mapped_column(Text)
    chunk_schema_version: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[date]
    fact_type: Mapped[str] = mapped_column(Text)
    fact_key: Mapped[str] = mapped_column(Text)
    fact_value: Mapped[str] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)


class RetrievedEvidence(Base):
    __tablename__ = "retrieved_evidence"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "risk_type",
            "chunk_id",
            name="uq_retrieved_evidence_run_risk_chunk",
        ),
        CheckConstraint(
            "risk_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_retrieved_evidence_risk_type",
        ),
        CheckConstraint("rank > 0", name="ck_retrieved_evidence_rank"),
        Index("ix_retrieved_evidence_run_id", "run_id"),
        Index("ix_retrieved_evidence_chunk_id", "chunk_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "workflow_runs.id",
            name="fk_retrieved_evidence_run",
            ondelete="RESTRICT",
        )
    )
    risk_type: Mapped[str] = mapped_column(Text)
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "evidence_chunks.id",
            name="fk_retrieved_evidence_chunk",
            ondelete="RESTRICT",
        )
    )
    rank: Mapped[int]
