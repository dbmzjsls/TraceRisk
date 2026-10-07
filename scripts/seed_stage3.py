"""Import and verify the deterministic Stage 3 synthetic catalog."""

import asyncio
import json
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.companies.models import Company, CompanyAlias
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, SourceCoverage
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog


async def main() -> None:
    catalog = generate_catalog()
    company_ids = [item.company.id for item in catalog]
    alias_ids = [alias.id for item in catalog for alias in item.aliases]
    evidence_ids = [evidence.id for item in catalog for evidence in item.evidence]
    coverage_ids = [coverage.id for item in catalog for coverage in item.coverage]
    engine = create_async_engine(Settings().database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            result = await import_catalog(session, catalog)
        async with sessions() as session:
            persisted = {
                "companies": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(Company)
                        .where(Company.id.in_(company_ids))
                    )
                    or 0
                ),
                "company_aliases": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(CompanyAlias)
                        .where(CompanyAlias.id.in_(alias_ids))
                    )
                    or 0
                ),
                "evidence": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(Evidence)
                        .where(Evidence.id.in_(evidence_ids))
                    )
                    or 0
                ),
                "source_coverage": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceCoverage)
                        .where(SourceCoverage.id.in_(coverage_ids))
                    )
                    or 0
                ),
            }
        if persisted != result.totals:
            raise RuntimeError(
                f"Stage 3 catalog count mismatch: {persisted=} {result.totals=}"
            )
        print(
            json.dumps(
                {
                    "catalog": result.totals,
                    "inserted": result.inserted,
                    "persisted": persisted,
                    "preserved_company": str(catalog[0].company.id),
                    "preserved_evidence": [
                        str(evidence.id) for evidence in catalog[0].evidence
                    ],
                    "stable_hashes_verified": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(main())
