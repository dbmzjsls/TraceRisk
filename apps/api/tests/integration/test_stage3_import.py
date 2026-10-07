from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.companies.models import Company, CompanyAlias
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, SourceCoverage
from tracerisk.rules.engine import evaluate_intellectual_property_rule
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import SyntheticDataConflict, import_catalog

pytestmark = pytest.mark.anyio

EXPECTED_CHECKED_AT = datetime(2026, 9, 11, tzinfo=UTC)


async def _counts(session: AsyncSession) -> dict[str, int]:
    return {
        model.__tablename__: int(
            await session.scalar(select(func.count()).select_from(model)) or 0
        )
        for model in (Company, CompanyAlias, Evidence, SourceCoverage)
    }


async def test_models_enforce_alias_and_coverage_constraints(
    database: Settings,
) -> None:
    assert {
        foreign_key.target_fullname
        for foreign_key in CompanyAlias.__table__.foreign_keys
    } == {"companies.id"}
    assert {
        foreign_key.target_fullname
        for foreign_key in SourceCoverage.__table__.foreign_keys
    } == {"companies.id"}
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    company_a = Company(
        synthetic_key="model-a",
        name="模型甲（合成）",
        unified_credit_code="MODEL-A",
        is_synthetic=True,
    )
    company_b = Company(
        synthetic_key="model-b",
        name="模型乙（合成）",
        unified_credit_code="MODEL-B",
        is_synthetic=True,
    )
    try:
        async with sessions.begin() as session:
            session.add_all([company_a, company_b])
            await session.flush()
            session.add_all(
                [
                    CompanyAlias(
                        company_id=company_a.id,
                        name="共享旧称",
                        alias_type="historical",
                    ),
                    CompanyAlias(
                        company_id=company_b.id,
                        name="共享旧称",
                        alias_type="historical",
                    ),
                ]
            )
        async with sessions() as session:
            async with session.begin():
                session.add(
                    CompanyAlias(
                        company_id=company_a.id,
                        name="共享旧称",
                        alias_type="historical",
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
        async with sessions() as session:
            async with session.begin():
                session.add(
                    SourceCoverage(
                        company_id=company_a.id,
                        source_type="invalid",
                        as_of_date=EXPECTED_CHECKED_AT.date(),
                        status="available",
                        checked_at=EXPECTED_CHECKED_AT,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
        async with sessions() as session:
            async with session.begin():
                session.add(
                    SourceCoverage(
                        company_id=company_a.id,
                        source_type="business",
                        as_of_date=EXPECTED_CHECKED_AT.date(),
                        status="invalid",
                        checked_at=EXPECTED_CHECKED_AT,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
        async with sessions.begin() as session:
            session.add(
                SourceCoverage(
                    company_id=company_a.id,
                    source_type="business",
                    as_of_date=EXPECTED_CHECKED_AT.date(),
                    status="available",
                    checked_at=EXPECTED_CHECKED_AT,
                )
            )
        async with sessions() as session:
            async with session.begin():
                session.add(
                    SourceCoverage(
                        company_id=company_a.id,
                        source_type="business",
                        as_of_date=EXPECTED_CHECKED_AT.date(),
                        status="stale",
                        checked_at=EXPECTED_CHECKED_AT,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
    finally:
        await engine.dispose()


async def test_import_is_exact_preserving_and_idempotent(database: Settings) -> None:
    catalog = generate_catalog()
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preserved = catalog[0]
        async with sessions.begin() as session:
            session.add(
                Company(
                    id=preserved.company.id,
                    synthetic_key=preserved.company.synthetic_key,
                    name=preserved.company.name,
                    unified_credit_code=preserved.company.unified_credit_code,
                    is_synthetic=preserved.company.is_synthetic,
                )
            )
            session.add_all(
                [
                    Evidence(
                        id=value.id,
                        company_id=value.company_id,
                        source_type=value.source_type.value,
                        risk_type=value.risk_type.value,
                        source_uri=value.source_uri,
                        title=value.title,
                        observed_at=value.observed_at,
                        fact_type=value.fact_type,
                        fact_key=value.fact_key,
                        fact_value=value.fact_value,
                        polarity=value.polarity,
                        content=value.content,
                        content_hash=value.content_hash,
                    )
                    for value in preserved.evidence
                ]
            )
        async with sessions.begin() as session:
            first = await import_catalog(session, catalog)
        async with sessions.begin() as session:
            first_counts = await _counts(session)
            second = await import_catalog(session, catalog)
            second_counts = await _counts(session)

            assert first.inserted == {
                "companies": 9,
                "company_aliases": first_counts["company_aliases"],
                "evidence": first_counts["evidence"] - 3,
                "source_coverage": first_counts["source_coverage"],
            }
            assert second.inserted == {key: 0 for key in first_counts}
            assert (
                second_counts
                == first_counts
                == {
                    "companies": 10,
                    "company_aliases": sum(len(item.aliases) for item in catalog),
                    "evidence": sum(len(item.evidence) for item in catalog),
                    "source_coverage": 40,
                }
            )

            companies = (await session.scalars(select(Company))).all()
            aliases = (await session.scalars(select(CompanyAlias))).all()
            evidence = (await session.scalars(select(Evidence))).all()
            coverage = (await session.scalars(select(SourceCoverage))).all()
            assert {
                row.id: (
                    row.synthetic_key,
                    row.name,
                    row.unified_credit_code,
                    row.is_synthetic,
                )
                for row in companies
            } == {
                item.company.id: (
                    item.company.synthetic_key,
                    item.company.name,
                    item.company.unified_credit_code,
                    item.company.is_synthetic,
                )
                for item in catalog
            }
            assert {
                (row.id, row.company_id, row.name, row.alias_type) for row in aliases
            } == {
                (alias.id, alias.company_id, alias.name, "historical")
                for item in catalog
                for alias in item.aliases
            }
            assert {
                (
                    row.id,
                    row.company_id,
                    row.source_type,
                    row.risk_type,
                    row.source_uri,
                    row.title,
                    row.observed_at,
                    row.fact_type,
                    row.fact_key,
                    row.fact_value,
                    row.polarity,
                    row.content,
                    row.content_hash,
                )
                for row in evidence
            } == {
                (
                    value.id,
                    value.company_id,
                    value.source_type.value,
                    value.risk_type.value,
                    value.source_uri,
                    value.title,
                    value.observed_at,
                    value.fact_type,
                    value.fact_key,
                    value.fact_value,
                    value.polarity,
                    value.content,
                    value.content_hash,
                )
                for item in catalog
                for value in item.evidence
            }
            assert {
                (
                    row.id,
                    row.company_id,
                    row.source_type,
                    row.as_of_date,
                    row.status,
                    row.checked_at,
                )
                for row in coverage
            } == {
                (
                    value.id,
                    value.company_id,
                    value.source_type.value,
                    value.as_of_date,
                    value.status.value,
                    EXPECTED_CHECKED_AT,
                )
                for item in catalog
                for value in item.coverage
            }

            company = await session.get(Company, preserved.company.id)
            assert company is not None
            assert (
                company.id,
                company.synthetic_key,
                company.name,
                company.unified_credit_code,
                company.is_synthetic,
            ) == (
                preserved.company.id,
                "judicial-open-case-story",
                preserved.company.name,
                preserved.company.unified_credit_code,
                True,
            )
            for expected in preserved.evidence:
                row = await session.get(Evidence, expected.id)
                assert row is not None
                assert tuple(
                    getattr(row, field) for field in Evidence.__table__.columns.keys()
                ) == (
                    expected.id,
                    expected.company_id,
                    expected.source_type.value,
                    expected.risk_type.value,
                    expected.source_uri,
                    expected.title,
                    expected.observed_at,
                    expected.fact_type,
                    expected.fact_key,
                    expected.fact_value,
                    expected.polarity,
                    expected.content,
                    expected.content_hash,
                )
    finally:
        await engine.dispose()


ConflictSetup = Callable[[AsyncSession], Awaitable[dict[str, int]]]


async def _company_conflict(session: AsyncSession) -> dict[str, int]:
    expected = generate_catalog()[0].company
    session.add(
        Company(
            id=expected.id,
            synthetic_key=expected.synthetic_key,
            name="篡改名称",
            unified_credit_code=expected.unified_credit_code,
            is_synthetic=expected.is_synthetic,
        )
    )
    await session.flush()
    return await _counts(session)


async def _alias_conflict(session: AsyncSession) -> dict[str, int]:
    scenario = next(item for item in generate_catalog() if item.aliases)
    company = scenario.company
    alias = scenario.aliases[0]
    session.add(
        Company(
            id=company.id,
            synthetic_key=company.synthetic_key,
            name=company.name,
            unified_credit_code=company.unified_credit_code,
            is_synthetic=company.is_synthetic,
        )
    )
    await session.flush()
    session.add(
        CompanyAlias(
            id=alias.id,
            company_id=alias.company_id,
            name="篡改旧称",
            alias_type="historical",
        )
    )
    await session.flush()
    return await _counts(session)


async def _evidence_conflict(session: AsyncSession) -> dict[str, int]:
    expected = generate_catalog()[0]
    company = expected.company
    evidence = expected.evidence[0]
    session.add(
        Company(
            id=company.id,
            synthetic_key=company.synthetic_key,
            name=company.name,
            unified_credit_code=company.unified_credit_code,
            is_synthetic=company.is_synthetic,
        )
    )
    await session.flush()
    values = {
        column: getattr(evidence, column)
        for column in Evidence.__table__.columns.keys()
    }
    values["source_type"] = evidence.source_type.value
    values["risk_type"] = evidence.risk_type.value
    values["title"] = "篡改标题"
    session.add(Evidence(**values))
    await session.flush()
    return await _counts(session)


async def _coverage_conflict(session: AsyncSession) -> dict[str, int]:
    expected = generate_catalog()[0]
    company = expected.company
    coverage = expected.coverage[0]
    session.add(
        Company(
            id=company.id,
            synthetic_key=company.synthetic_key,
            name=company.name,
            unified_credit_code=company.unified_credit_code,
            is_synthetic=company.is_synthetic,
        )
    )
    await session.flush()
    session.add(
        SourceCoverage(
            id=coverage.id,
            company_id=coverage.company_id,
            source_type=coverage.source_type.value,
            as_of_date=coverage.as_of_date,
            status="stale",
            checked_at=EXPECTED_CHECKED_AT,
        )
    )
    await session.flush()
    return await _counts(session)


async def _company_synthetic_key_collision(session: AsyncSession) -> dict[str, int]:
    expected = generate_catalog()[0].company
    session.add(
        Company(
            id=uuid4(),
            synthetic_key=expected.synthetic_key,
            name="自然键占用企业",
            unified_credit_code="NATURAL-KEY-OCCUPIER",
            is_synthetic=True,
        )
    )
    await session.flush()
    return await _counts(session)


async def _company_credit_code_collision(session: AsyncSession) -> dict[str, int]:
    expected = generate_catalog()[0].company
    session.add(
        Company(
            id=uuid4(),
            synthetic_key="natural-credit-code-occupier",
            name="信用代码占用企业",
            unified_credit_code=expected.unified_credit_code,
            is_synthetic=True,
        )
    )
    await session.flush()
    return await _counts(session)


async def _alias_natural_key_collision(session: AsyncSession) -> dict[str, int]:
    scenario = next(item for item in generate_catalog() if item.aliases)
    company = scenario.company
    alias = scenario.aliases[0]
    session.add(
        Company(
            id=company.id,
            synthetic_key=company.synthetic_key,
            name=company.name,
            unified_credit_code=company.unified_credit_code,
            is_synthetic=company.is_synthetic,
        )
    )
    await session.flush()
    session.add(
        CompanyAlias(
            id=uuid4(),
            company_id=alias.company_id,
            name=alias.name,
            alias_type="historical",
        )
    )
    await session.flush()
    return await _counts(session)


async def _evidence_natural_key_collision(session: AsyncSession) -> dict[str, int]:
    scenario = generate_catalog()[0]
    company = scenario.company
    evidence = scenario.evidence[0]
    session.add(
        Company(
            id=company.id,
            synthetic_key=company.synthetic_key,
            name=company.name,
            unified_credit_code=company.unified_credit_code,
            is_synthetic=company.is_synthetic,
        )
    )
    await session.flush()
    values = {
        column: getattr(evidence, column)
        for column in Evidence.__table__.columns.keys()
    }
    values["id"] = uuid4()
    values["source_type"] = evidence.source_type.value
    values["risk_type"] = evidence.risk_type.value
    session.add(Evidence(**values))
    await session.flush()
    return await _counts(session)


async def _coverage_natural_key_collision(session: AsyncSession) -> dict[str, int]:
    scenario = generate_catalog()[0]
    company = scenario.company
    coverage = scenario.coverage[0]
    session.add(
        Company(
            id=company.id,
            synthetic_key=company.synthetic_key,
            name=company.name,
            unified_credit_code=company.unified_credit_code,
            is_synthetic=company.is_synthetic,
        )
    )
    await session.flush()
    session.add(
        SourceCoverage(
            id=uuid4(),
            company_id=coverage.company_id,
            source_type=coverage.source_type.value,
            as_of_date=coverage.as_of_date,
            status=coverage.status.value,
            checked_at=EXPECTED_CHECKED_AT,
        )
    )
    await session.flush()
    return await _counts(session)


@pytest.mark.parametrize(
    "setup",
    [
        _company_conflict,
        _alias_conflict,
        _evidence_conflict,
        _coverage_conflict,
        _company_synthetic_key_collision,
        _company_credit_code_collision,
        _alias_natural_key_collision,
        _evidence_natural_key_collision,
        _coverage_natural_key_collision,
    ],
    ids=[
        "company-id-mismatch",
        "alias-id-mismatch",
        "evidence-id-mismatch",
        "coverage-id-mismatch",
        "company-synthetic-key-collision",
        "company-credit-code-collision",
        "alias-natural-key-collision",
        "evidence-natural-key-collision",
        "coverage-natural-key-collision",
    ],
)
async def test_conflict_rolls_back_all_catalog_additions(
    database: Settings, setup: ConflictSetup
) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            before = await setup(session)
        with pytest.raises(SyntheticDataConflict):
            async with sessions.begin() as session:
                await import_catalog(session, generate_catalog())
        async with sessions() as session:
            assert await _counts(session) == before
    finally:
        await engine.dispose()


async def test_importer_does_not_commit_caller_transaction(database: Settings) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        session = sessions()
        try:
            await import_catalog(session, generate_catalog())
            await session.rollback()
        finally:
            await session.close()
        async with sessions() as session:
            assert await _counts(session) == {
                "companies": 0,
                "company_aliases": 0,
                "evidence": 0,
                "source_coverage": 0,
            }
    finally:
        await engine.dispose()


async def test_imported_core_ip_fact_loads_from_database_and_triggers_rule(
    database: Settings,
) -> None:
    catalog = generate_catalog()
    full_score = next(item for item in catalog if item.key == "all-rules-95")
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            await import_catalog(session, catalog)
            result = await evaluate_intellectual_property_rule(
                session,
                company_id=full_score.company.id,
                as_of_date=full_score.coverage[0].as_of_date,
            )

        assert result.score == 15
        assert result.matched_fact_keys == ("FULL-IP-CORE-001",)
        expected = next(
            value
            for value in full_score.evidence
            if value.fact_type == "core_ip_status"
        )
        assert tuple(
            (reference.evidence_id, reference.content_hash)
            for reference in result.evidence_references
        ) == ((expected.id, expected.content_hash),)
    finally:
        await engine.dispose()
