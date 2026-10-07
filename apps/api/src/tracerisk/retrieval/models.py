"""Versioned document and fixed-query vectors; provider identity is immutable."""

from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class EmbeddingIndexVersion(Base):
    __tablename__ = "embedding_index_versions"
    __table_args__ = (
        CheckConstraint("dimensions = 512", name="ck_embedding_index_dimensions"),
        CheckConstraint("distance = 'cosine'", name="ck_embedding_index_distance"),
        Index(
            "uq_embedding_index_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_key: Mapped[str] = mapped_column(Text, unique=True)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    dimensions: Mapped[int]
    distance: Mapped[str] = mapped_column(Text)
    chunk_schema_version: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence_chunks.id", ondelete="RESTRICT"), primary_key=True
    )
    index_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_index_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(512))


class IndexQueryEmbedding(Base):
    __tablename__ = "index_query_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "index_version_id",
            "query_version",
            "risk_type",
            name="uq_index_query_version_risk",
        ),
        CheckConstraint("query_hash ~ '^[0-9a-f]{64}$'", name="ck_index_query_hash"),
        CheckConstraint(
            "risk_type IN ('business', 'judicial', 'news', 'intellectual_property')",
            name="ck_index_query_risk_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    index_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_index_versions.id", ondelete="RESTRICT")
    )
    query_version: Mapped[str] = mapped_column(Text)
    risk_type: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(512))
