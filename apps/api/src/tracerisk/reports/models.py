from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracerisk.db.base import Base


class RiskReport(Base):
    __tablename__ = "risk_reports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), unique=True)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    html_content: Mapped[str] = mapped_column(Text, default="")
