from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    unified_credit_code: str
    is_synthetic: bool


class CompanyListResponse(BaseModel):
    notice: str = "合成数据，仅供演示"
    items: list[CompanyResponse]
