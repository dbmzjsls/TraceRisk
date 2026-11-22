"""Load the verified Suzhou Bank disclosure into the dedicated public-demo database."""

import asyncio
import sys
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, SourceCoverage
from tracerisk.retrieval.embeddings import BGEEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index

DATABASE = "tracerisk_public_demo"
PUBLISHED_AT = date(2026, 6, 25)
CREDIT_CODE = "91320000768299855B"
COMPANY_ID = uuid5(NAMESPACE_URL, f"tracerisk:public:{CREDIT_CODE}")
SOURCE = "https://static.cninfo.com.cn/finalpage/2026-06-25/1225386155.PDF#page=1"
# 公告 2026-026 第 1 页特别提示。保留原文措辞，按披露日期控制可见性。
EXCERPT = (
    "案件所处的诉讼阶段：一审，尚未开庭审理。\n"
    "上市公司所处的当事人地位：苏州银行股份有限公司（以下简称“本行”）为被告之一。"
)


async def seed() -> None:
    settings = Settings()
    if settings.postgres_db != DATABASE or settings.embedding_provider != "bge":
        raise ValueError("Select the dedicated public-demo database and BGE embeddings")
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            if await session.scalar(text("SELECT current_database()")) != DATABASE:
                raise ValueError("Connected to the wrong database")
            if await session.scalar(
                select(Company.id).where(Company.is_synthetic).limit(1)
            ):
                raise ValueError("Public demo database contains synthetic companies")

            company = dict(
                id=COMPANY_ID,
                synthetic_key=f"public:{CREDIT_CODE}",
                name="苏州银行股份有限公司",
                unified_credit_code=CREDIT_CODE,
                is_synthetic=False,
            )
            evidence = dict(
                id=uuid5(NAMESPACE_URL, SOURCE),
                company_id=COMPANY_ID,
                source_type="judicial",
                risk_type="judicial",
                source_uri=SOURCE,
                title="苏州银行诉讼事项进展公告（2026-026，第1页）",
                observed_at=PUBLISHED_AT,
                fact_type="judicial_case_status",
                fact_key="（2024）内06民初70号",
                fact_value="open",
                polarity="negative",
                content=EXCERPT,
                content_hash=sha256(EXCERPT.encode()).hexdigest(),
            )
            coverage = dict(
                id=uuid5(NAMESPACE_URL, SOURCE + ":coverage"),
                company_id=COMPANY_ID,
                source_type="judicial",
                as_of_date=PUBLISHED_AT,
                status="available",
                checked_at=datetime.now(UTC),
            )
            for model, values in (
                (Company, company),
                (Evidence, evidence),
                (SourceCoverage, coverage),
            ):
                await session.execute(
                    insert(model).values(**values).on_conflict_do_nothing()
                )
                stored = await session.get(model, values["id"])
                if stored is None or any(
                    getattr(stored, key) != value
                    for key, value in values.items()
                    if key != "checked_at"
                ):
                    raise ValueError(
                        f"Conflicting public demo record in {model.__tablename__}"
                    )

            index = await build_index(
                session, BGEEmbedding(), IndexContract.from_settings(settings)
            )
            version_key = index.version_key
        print(f"Loaded one company, one disclosure, and BGE index: {version_key}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop) as runner:
        runner.run(seed())
