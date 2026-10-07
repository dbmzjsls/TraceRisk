from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.auth.router import router as auth_router
from tracerisk.companies.router import router as companies_router
from tracerisk.config import Settings
from tracerisk.investigations.router import router as investigations_router
from tracerisk.reports.router import router as reports_router
from tracerisk.retrieval.indexing import IndexContract, check_index
from tracerisk.reviews.router import router as reviews_router
from tracerisk.reviews.run_router import router as run_reviews_router
from tracerisk.workflows.sse import router as workflow_events_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.settings = Settings()
    engine = create_async_engine(app.state.settings.database_url, pool_pre_ping=True)
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with app.state.session_factory() as session:
            await check_index(
                session, IndexContract.from_settings(app.state.settings), required=False
            )
        yield
    finally:
        await engine.dispose()


app = FastAPI(
    title="TraceRisk API",
    description="合成数据，仅供演示",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(companies_router)
app.include_router(reports_router)
app.include_router(reviews_router)
app.include_router(investigations_router)
app.include_router(run_reviews_router)
app.include_router(workflow_events_router)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Process liveness only; database readiness is checked separately."""
    return HealthResponse()
