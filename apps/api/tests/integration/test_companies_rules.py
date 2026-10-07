from collections.abc import AsyncIterator
from datetime import date
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from tracerisk.auth.seed import seed_accounts
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence
from tracerisk.investigations.service import create_investigation
from tracerisk.main import app
from tracerisk.rules.engine import (
    evaluate_all_rules,
    evaluate_judicial_rule,
    save_rule_finding,
    save_rule_findings,
)
from tracerisk.rules.models import RuleFinding, RuleFindingEvidence
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog

pytestmark = pytest.mark.anyio
SECRET = "company-rule-integration-signing-secret-at-least-48-characters"


@pytest.fixture
async def client(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            await seed_accounts(session)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as connection:
            yield connection


async def analyst_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/auth/login",
        json={"username": "analyst", "password": "AnalystDemo!2026"},
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


async def test_company_routes_require_login_and_return_synthetic_company(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/companies")).status_code == 401
    async with app.state.session_factory.begin() as session:
        company = Company(
            synthetic_key="test-company",
            name="测试合成企业",
            unified_credit_code="SYNTHETIC-TEST-001",
        )
        session.add(company)
        await session.flush()
        company_id = company.id
    headers = {"Authorization": f"Bearer {await analyst_token(client)}"}
    response = await client.get("/companies", headers=headers)
    assert response.status_code == 200
    assert response.json()["notice"] == "合成数据，仅供演示"
    assert response.json()["items"] == [
        {
            "id": str(company_id),
            "name": "测试合成企业",
            "unified_credit_code": "SYNTHETIC-TEST-001",
            "is_synthetic": True,
        }
    ]
    assert (
        await client.get(f"/companies/{company_id}", headers=headers)
    ).status_code == 200
    assert (
        await client.get(f"/companies/{uuid4()}", headers=headers)
    ).status_code == 404


async def test_rule_snapshot_is_idempotent_and_keeps_hashed_evidence_references(
    client: httpx.AsyncClient,
) -> None:
    as_of_date = date(2026, 9, 11)
    async with app.state.session_factory.begin() as session:
        company = Company(
            synthetic_key="rule-company",
            name="司法规则合成企业",
            unified_credit_code="SYNTHETIC-RULE-001",
        )
        session.add(company)
        await session.flush()
        evidence = [
            Evidence(
                company_id=company.id,
                source_type="judicial",
                risk_type="judicial",
                source_uri=f"synthetic://test/{number}",
                title=f"合成案件 {number}",
                observed_at=as_of_date,
                fact_type="judicial_case_status",
                fact_key=f"SYN-CASE-{number}",
                fact_value="open",
                polarity="negative",
                content=f"合成案件 {number} 未结案",
                content_hash=f"{number:064x}",
            )
            for number in (1, 2)
        ]
        session.add_all(evidence)
        investigation = await create_investigation(
            session,
            company_id=company.id,
            created_by=uuid4(),
            as_of_date=as_of_date,
        )
        evaluation = await evaluate_judicial_rule(
            session, company_id=company.id, as_of_date=as_of_date
        )
        first = await save_rule_finding(
            session, run_id=investigation.current_run_id, evaluation=evaluation
        )
        second = await save_rule_finding(
            session, run_id=investigation.current_run_id, evaluation=evaluation
        )
        assert first is not None and second is not None
        assert first.id == second.id
        assert first.rule_code == "JUDICIAL_OPEN_CASE_V1"
        assert first.rule_version == "1"
        assert first.score == 35
        assert first.parameters["dedup_key"] == "case_number"
        assert await session.scalar(select(func.count()).select_from(RuleFinding)) == 1
        references = (
            await session.scalars(
                select(RuleFindingEvidence).order_by(RuleFindingEvidence.evidence_id)
            )
        ).all()
        assert {(item.evidence_id, item.content_hash) for item in references} == {
            (item.id, item.content_hash) for item in evidence
        }
        changed = evaluation.__class__(
            rule_code=evaluation.rule_code,
            rule_version=evaluation.rule_version,
            score=evaluation.score,
            parameters=evaluation.parameters,
            matched_fact_keys=evaluation.matched_fact_keys[:1],
            evidence_references=evaluation.evidence_references[:1],
        )
        with pytest.raises(ValueError, match="different evidence references"):
            await save_rule_finding(
                session, run_id=investigation.current_run_id, evaluation=changed
            )


async def test_full_catalog_company_evaluates_and_persists_fixed_95_points(
    client: httpx.AsyncClient,
) -> None:
    catalog = generate_catalog()
    scenario = next(item for item in catalog if item.key == "all-rules-95")
    async with app.state.session_factory.begin() as session:
        await import_catalog(session, catalog)
        investigation = await create_investigation(
            session,
            company_id=scenario.company.id,
            created_by=uuid4(),
            as_of_date=date(2026, 9, 11),
        )
        score = await evaluate_all_rules(
            session,
            company_id=scenario.company.id,
            as_of_date=date(2026, 9, 11),
        )
        first = await save_rule_findings(
            session, run_id=investigation.current_run_id, score=score
        )
        second = await save_rule_findings(
            session, run_id=investigation.current_run_id, score=score
        )

        assert score.total_score == 95
        assert tuple(item.rule_code for item in score.evaluations) == (
            "JUDICIAL_OPEN_CASE_V1",
            "BUSINESS_ABNORMAL_V1",
            "NEGATIVE_NEWS_V1",
            "CORE_IP_ANOMALY_V1",
        )
        assert tuple(item.id for item in first) == tuple(item.id for item in second)
        assert len(first) == 4
        assert (
            int(
                await session.scalar(
                    select(func.sum(RuleFinding.score)).where(
                        RuleFinding.run_id == investigation.current_run_id
                    )
                )
                or 0
            )
            == 95
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RuleFindingEvidence)
                .join(RuleFinding)
                .where(RuleFinding.run_id == investigation.current_run_id)
            )
            == 5
        )
