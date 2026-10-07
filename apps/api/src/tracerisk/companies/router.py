from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from tracerisk.auth.models import Role, User
from tracerisk.auth.router import Session, require_roles
from tracerisk.companies.models import Company
from tracerisk.companies.schemas import CompanyListResponse, CompanyResponse

router = APIRouter(prefix="/companies", tags=["companies"])
CompanyReader = Annotated[
    User, Depends(require_roles(Role.ANALYST, Role.REVIEWER, Role.ADMIN))
]


@router.get("", response_model=CompanyListResponse)
async def list_companies(session: Session, _user: CompanyReader) -> CompanyListResponse:
    companies = (
        await session.scalars(select(Company).order_by(Company.name, Company.id))
    ).all()
    return CompanyListResponse(
        items=[CompanyResponse.model_validate(company) for company in companies]
    )


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID, session: Session, _user: CompanyReader
) -> CompanyResponse:
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return CompanyResponse.model_validate(company)
