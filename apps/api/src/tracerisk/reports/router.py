from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from tracerisk.auth.models import Role, User
from tracerisk.auth.router import Session, require_roles
from tracerisk.reports.models import RiskReport
from tracerisk.reports.schemas import ReportResponse

router = APIRouter(prefix="/reports", tags=["reports"])
ReportReader = Annotated[
    User, Depends(require_roles(Role.ANALYST, Role.REVIEWER, Role.ADMIN))
]


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: UUID,
    session: Session,
    _user: ReportReader,
) -> ReportResponse:
    report = await session.get(RiskReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportResponse.model_validate(report)
