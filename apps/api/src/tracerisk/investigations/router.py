from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, true

from tracerisk.auth.models import Role, User
from tracerisk.auth.router import Session, require_roles
from tracerisk.companies.models import Company
from tracerisk.investigations.models import Investigation, WorkflowRun
from tracerisk.investigations.read_model import (
    load_investigation_detail,
    load_investigation_summary,
)
from tracerisk.investigations.schemas import (
    InvestigationCreateRequest,
    InvestigationDetailResponse,
    InvestigationListResponse,
    InvestigationResponse,
    RerunResponse,
)
from tracerisk.investigations.service import (
    CompanyNotFound,
    CreationConflict,
    InvestigationNotFound,
    RerunConflict,
    create_authenticated_investigation,
    create_rerun,
)
from tracerisk.reviews.router import IdempotencyKey

router = APIRouter(prefix="/investigations", tags=["investigations"])
InvestigationReader = Annotated[
    User, Depends(require_roles(Role.ANALYST, Role.REVIEWER, Role.ADMIN))
]
InvestigationCreator = Annotated[User, Depends(require_roles(Role.ANALYST, Role.ADMIN))]


@router.post("", response_model=InvestigationResponse)
async def create(
    payload: InvestigationCreateRequest,
    idempotency_key: IdempotencyKey,
    user: InvestigationCreator,
    session: Session,
    request: Request,
) -> InvestigationResponse:
    try:
        investigation = await create_authenticated_investigation(
            request.app.state.session_factory,
            company_id=payload.company_id,
            created_by=user.id,
            as_of_date=payload.as_of_date,
            idempotency_key=idempotency_key,
        )
    except CompanyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except CreationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return await load_investigation_summary(session, investigation)


@router.post("/{investigation_id}/runs", response_model=RerunResponse)
async def rerun(
    investigation_id: UUID,
    idempotency_key: IdempotencyKey,
    user: InvestigationCreator,
    request: Request,
) -> RerunResponse:
    try:
        replay = await create_rerun(
            request.app.state.session_factory,
            investigation_id=investigation_id,
            actor_id=user.id,
            idempotency_key=idempotency_key,
        )
    except InvestigationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RerunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return RerunResponse(id=replay.run_id, run_number=replay.run_number)


@router.get("", response_model=InvestigationListResponse)
async def list_investigations(
    session: Session,
    _user: InvestigationReader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    company_id: UUID | None = None,
) -> InvestigationListResponse:
    investigations = await session.scalars(
        select(Investigation)
        .join(Company, Company.id == Investigation.company_id)
        .join(WorkflowRun, WorkflowRun.id == Investigation.current_run_id)
        .where(Company.id == company_id if company_id is not None else true())
        .order_by(Investigation.as_of_date.desc(), Investigation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return InvestigationListResponse(
        items=[
            await load_investigation_summary(session, investigation)
            for investigation in investigations
        ]
    )


@router.get("/{investigation_id}", response_model=InvestigationDetailResponse)
async def detail(
    investigation_id: UUID,
    session: Session,
    _user: InvestigationReader,
) -> InvestigationDetailResponse:
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return await load_investigation_detail(session, investigation)
