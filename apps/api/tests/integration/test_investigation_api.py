import asyncio
from collections.abc import AsyncIterator
from datetime import date
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from tracerisk.ai.models import AIFinding
from tracerisk.auth.models import User
from tracerisk.auth.seed import seed_accounts
from tracerisk.auth.service import create_access_token
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.evidence.service import RetrievalIntegrityError, _retrieval_manifest
from tracerisk.investigations.models import (
    DispatchOutbox,
    Investigation,
    WorkflowRun,
    WorkflowStep,
)
from tracerisk.investigations.service import create_investigation
from tracerisk.main import app
from tracerisk.reports.models import RiskReport
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.models import RuleFinding, RuleFindingEvidence
from tracerisk.rules.references import resolve_rule_evidence_targets

SECRET = "investigation-api-test-secret-at-least-48-characters"
AI_CITATION = {
    "evidence_id": "21000000-0000-0000-0000-000000000001",
    "chunk_id": "00000000-0000-4000-8000-000000000006",
    "content_hash": "c" * 64,
    "quoted_excerpt": "Exact persisted excerpt",
}
pytestmark = pytest.mark.anyio


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


async def identity(username: str) -> tuple[UUID, dict[str, str]]:
    async with app.state.session_factory() as session:
        user = await session.scalar(select(User).where(User.username == username))
        assert user is not None
        return user.id, {
            "Authorization": f"Bearer {create_access_token(user.id, SECRET)}"
        }


async def company() -> UUID:
    async with app.state.session_factory.begin() as session:
        row = Company(
            synthetic_key=str(uuid4()),
            name="API synthetic company",
            unified_credit_code=str(uuid4()),
        )
        session.add(row)
        await session.flush()
        return row.id


async def create(
    client: httpx.AsyncClient,
    target_company_id: UUID,
    headers: dict[str, str],
    key: str = "creation-1",
    **changes: object,
) -> httpx.Response:
    return await client.post(
        "/investigations",
        headers={**headers, "Idempotency-Key": key},
        json={
            "company_id": str(target_company_id),
            "as_of_date": "2026-09-11",
            **changes,
        },
    )


async def review(
    client: httpx.AsyncClient,
    run_id: UUID,
    headers: dict[str, str],
    key: str = "run-review-1",
    **changes: object,
) -> httpx.Response:
    return await client.post(
        f"/runs/{run_id}/review",
        headers={**headers, "Idempotency-Key": key},
        json={"decision": "approve", "reason": "Checked", **changes},
    )


async def awaiting_run(owner: UUID | None = None) -> tuple[UUID, UUID]:
    async with app.state.session_factory.begin() as session:
        investigation = await create_investigation(
            session,
            company_id=await company(),
            created_by=owner or uuid4(),
            as_of_date=date(2026, 9, 11),
        )
        run = await session.get(WorkflowRun, investigation.current_run_id)
        assert run is not None
        run.status = "awaiting_review"
        return investigation.id, run.id


async def add_finding(
    run_id: UUID,
    *,
    support_status: str = "indeterminate",
    support_reason: str = "legacy-stage3",
) -> UUID:
    async with app.state.session_factory.begin() as session:
        finding = AIFinding(
            run_id=run_id,
            finding_key="judicial:case",
            version=1,
            risk_type="judicial",
            claim_code="JUDICIAL_OPEN_CASE",
            title="Open case",
            summary="Summary",
            reasoning="Reasoning",
            citations=[AI_CITATION],
            evidence_sufficiency="sufficient",
            confidence_level="high",
            review_recommended=True,
            support_status=support_status,
            support_reason=support_reason,
        )
        session.add(finding)
        await session.flush()
        return finding.id


async def test_roles_missing_company_and_bounded_creation(
    client: httpx.AsyncClient,
) -> None:
    company_id = await company()
    assert (await create(client, company_id, {})).status_code == 401
    assert (await client.get("/investigations")).status_code == 401
    assert (await client.get(f"/investigations/{uuid4()}")).status_code == 401
    _, reviewer = await identity("reviewer")
    assert (await create(client, company_id, reviewer)).status_code == 403
    _, analyst = await identity("analyst")
    assert (await create(client, uuid4(), analyst)).status_code == 404
    for changes in (
        {"created_by": str(uuid4())},
        {"as_of_date": "invalid"},
        {"company_id": "invalid"},
        {"creation_idempotency_key": "in-body"},
    ):
        assert (await create(client, company_id, analyst, **changes)).status_code == 422
    for key in ("", "x" * 201):
        assert (await create(client, company_id, analyst, key)).status_code == 422
    for body in ({"company_id": str(company_id)}, {"as_of_date": "2026-09-11"}):
        assert (
            await client.post(
                "/investigations",
                headers={**analyst, "Idempotency-Key": "missing"},
                json=body,
            )
        ).status_code == 422
    assert (
        await client.post(
            "/investigations",
            headers=analyst,
            json={"company_id": str(company_id), "as_of_date": "2026-09-11"},
        )
    ).status_code == 422
    async with app.state.session_factory() as session:
        for model in (Investigation, WorkflowRun, DispatchOutbox):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    _, admin = await identity("admin")
    assert (await create(client, company_id, admin)).status_code == 200


async def test_creation_concurrency_actor_replay_and_legacy_nulls(
    client: httpx.AsyncClient,
) -> None:
    company_id = await company()
    actor_id, analyst = await identity("analyst")
    responses = await asyncio.gather(
        *[create(client, company_id, analyst) for _ in range(4)]
    )
    assert [response.status_code for response in responses] == [200] * 4
    original = responses[0].json()
    assert all(response.json() == original for response in responses)
    assert original["created_by"] == str(actor_id)
    run_id = UUID(original["current_run"]["id"])
    async with app.state.session_factory.begin() as session:
        for model in (Investigation, WorkflowRun, DispatchOutbox):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "completed"
    replay = await create(client, company_id, analyst)
    assert replay.status_code == 200 and replay.json()["id"] == original["id"]
    assert replay.json()["current_run"]["status"] == "completed"
    assert (
        await create(client, company_id, analyst, as_of_date="2026-09-12")
    ).status_code == 409
    # Compare the old key before checking whether a changed company exists.
    assert (await create(client, uuid4(), analyst)).status_code == 409
    _, admin = await identity("admin")
    assert (await create(client, company_id, admin)).status_code == 200
    async with app.state.session_factory.begin() as session:
        for _ in range(2):
            legacy = await create_investigation(
                session,
                company_id=company_id,
                created_by=actor_id,
                as_of_date=date(2026, 9, 11),
            )
            assert legacy.creation_idempotency_key is None


async def test_persisted_detail_list_status_and_report(
    client: httpx.AsyncClient,
) -> None:
    investigation_id, run_id = await awaiting_run()
    finding_id = await add_finding(run_id)
    digest = sha256(b"Persisted evidence").hexdigest()
    async with app.state.session_factory.begin() as session:
        investigation = await session.get(Investigation, investigation_id)
        assert investigation is not None
        evidence = Evidence(
            company_id=investigation.company_id,
            source_type="judicial",
            risk_type="judicial",
            source_uri="synthetic://api/case",
            title="Persisted source",
            observed_at=date(2026, 8, 1),
            fact_type="judicial_case_status",
            fact_key="case-1",
            fact_value="open",
            polarity="negative",
            content="Persisted evidence",
            content_hash=digest,
        )
        session.add(evidence)
        await session.flush()
        chunk = EvidenceChunk(
            source_type=evidence.source_type,
            risk_type=evidence.risk_type,
            evidence_content_hash=evidence.content_hash,
            chunk_schema_version="legacy-stage3",
            evidence_id=evidence.id,
            company_id=investigation.company_id,
            observed_at=evidence.observed_at,
            fact_type=evidence.fact_type,
            fact_key=evidence.fact_key,
            fact_value=evidence.fact_value,
            polarity=evidence.polarity,
            content=evidence.content,
            content_hash=digest,
        )
        rule = RuleFinding(
            run_id=run_id,
            rule_code="judicial",
            rule_version="1",
            score=35,
            parameters={"cases": 2},
        )
        session.add_all(
            [
                chunk,
                rule,
                WorkflowStep(run_id=run_id, name="rule", attempt_count=2),
                WorkflowStep(run_id=run_id, name="retrieve_evidence"),
            ]
        )
        await session.flush()
        manifest_name = _retrieval_manifest([("judicial", chunk.id, 1)])
        session.add_all(
            [
                WorkflowStep(run_id=run_id, name=manifest_name),
                RetrievedEvidence(
                    run_id=run_id, risk_type="judicial", chunk_id=chunk.id, rank=1
                ),
                RuleFindingEvidence(
                    rule_finding_id=rule.id,
                    evidence_id=evidence.id,
                    content_hash=digest,
                ),
                ReviewDecision(
                    run_id=run_id,
                    ai_finding_id=finding_id,
                    finding_version=1,
                    reviewer_id=uuid4(),
                    idempotency_key="persisted",
                    decision="reject",
                    reason="Stored reason",
                ),
            ]
        )
        report = RiskReport(
            run_id=run_id, snapshot={"rule_score": 35}, html_content="Stored"
        )
        session.add(report)
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "completed"
        await session.flush()
        report_id = report.id
        # Spike callers historically supplied opaque company IDs without company rows.
        legacy = await create_investigation(
            session,
            company_id=uuid4(),
            created_by=uuid4(),
            as_of_date=date(2026, 9, 11),
        )
        legacy_id = legacy.id
    for role in ("analyst", "reviewer", "admin"):
        _, headers = await identity(role)
        response = await client.get(
            f"/investigations/{investigation_id}", headers=headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["current_run"] == {
            "id": str(run_id),
            "run_number": 1,
            "status": "completed",
            "report_id": str(report_id),
        }
        assert body["steps"] == [
            {"name": "retrieve_evidence", "attempt_count": 1},
            {"name": "rule", "attempt_count": 2},
        ]
        assert body["rule_score"] == 35 and body["rule_findings"][0]["parameters"] == {
            "cases": 2
        }
        assert body["rule_findings"][0]["evidence"][0]["content_hash"] == digest
        assert body["rule_findings"][0]["evidence"][0]["chunk_id"] == str(chunk.id)
        assert body["rule_findings"][0]["evidence"][0]["chunk_content_hash"] == digest
        assert body["retrieved_evidence"][0]["content"] == "Persisted evidence"
        assert body["retrieval_state"] == "ready"
        assert body["coverage"]["entries"] == [
            {
                "source_type": source,
                "status": "missing",
                "coverage_id": None,
                "checked_at": None,
            }
            for source in ("business", "judicial", "news", "intellectual_property")
        ]
        assert body["ai_findings"][0]["finding_id"] == str(finding_id)
        assert body["ai_findings"][0]["citations"] == [AI_CITATION]
        assert body["ai_findings"][0]["support_status"] == "indeterminate"
        assert body["ai_findings"][0]["support_reason"] == "legacy-stage3"
        assert body["decisions"][0]["reason"] == "Stored reason"
        assert (
            "password_hash" not in response.text
            and "idempotency_key" not in response.text
        )
        listing = await client.get("/investigations?limit=1&offset=0", headers=headers)
        assert listing.status_code == 200 and listing.json()["items"][0]["id"] == str(
            investigation_id
        )
        assert (
            await client.get(f"/investigations/{uuid4()}", headers=headers)
        ).status_code == 404
        for query in ("limit=0", "limit=101", "offset=-1"):
            assert (
                await client.get(f"/investigations?{query}", headers=headers)
            ).status_code == 422
        assert (
            await client.get(f"/investigations/{legacy_id}", headers=headers)
        ).status_code == 404
        all_items = (await client.get("/investigations", headers=headers)).json()[
            "items"
        ]
        assert [item["id"] for item in all_items] == [str(investigation_id)]
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 2
        )
        assert await session.scalar(select(func.count()).select_from(WorkflowStep)) == 3
        assert await session.scalar(
            select(WorkflowStep.id).where(WorkflowStep.name == manifest_name)
        )


async def test_detail_retrieval_state_truth_table(client: httpx.AsyncClient) -> None:
    _, headers = await identity("analyst")
    company_id = await company()
    cases: list[tuple[str, str]] = []
    async with app.state.session_factory.begin() as session:
        for label in ("pending", "stepped", "terminal", "empty-ready", "partial"):
            investigation = await create_investigation(
                session,
                company_id=company_id,
                created_by=uuid4(),
                as_of_date=date(2026, 9, 11),
            )
            run = await session.get(WorkflowRun, investigation.current_run_id)
            assert run is not None
            if label == "stepped":
                session.add(WorkflowStep(run_id=run.id, name="prepare_context"))
            elif label == "terminal":
                run.status = "failed"
            elif label == "empty-ready":
                session.add_all(
                    [
                        WorkflowStep(run_id=run.id, name="retrieve_evidence"),
                        WorkflowStep(run_id=run.id, name=_retrieval_manifest([])),
                    ]
                )
            elif label == "partial":
                session.add(WorkflowStep(run_id=run.id, name="retrieve_evidence"))
            cases.append((label, str(investigation.id)))
    expected = {
        "pending": "pending",
        "stepped": "legacy_unavailable",
        "terminal": "legacy_unavailable",
        "empty-ready": "ready",
    }
    for label, investigation_id in cases[:-1]:
        response = await client.get(
            f"/investigations/{investigation_id}", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["retrieval_state"] == expected[label]
        source_types = [
            entry["source_type"] for entry in response.json()["coverage"]["entries"]
        ]
        assert source_types == ["business", "judicial", "news", "intellectual_property"]
    with pytest.raises(RetrievalIntegrityError, match="Partial frozen retrieval"):
        await client.get(f"/investigations/{cases[-1][1]}", headers=headers)


@pytest.mark.parametrize(
    ("marker", "manifest", "row"),
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, False, True),
        (False, True, True),
    ],
)
async def test_detail_rejects_every_partial_retrieval_combination(
    client: httpx.AsyncClient,
    marker: bool,
    manifest: bool,
    row: bool,
) -> None:
    investigation_id, run_id = await awaiting_run()
    async with app.state.session_factory.begin() as session:
        chunk_id: UUID | None = None
        if row:
            investigation = await session.get(Investigation, investigation_id)
            assert investigation is not None
            digest = sha256(b"partial retrieval").hexdigest()
            evidence = Evidence(
                company_id=investigation.company_id,
                source_type="judicial",
                risk_type="judicial",
                source_uri=f"synthetic://partial/{run_id}",
                title="Partial",
                observed_at=date(2026, 9, 1),
                fact_type="judicial_case_status",
                fact_key="partial",
                fact_value="open",
                polarity="negative",
                content="partial retrieval",
                content_hash=digest,
            )
            session.add(evidence)
            await session.flush()
            chunk = EvidenceChunk(
                evidence_id=evidence.id,
                company_id=evidence.company_id,
                source_type=evidence.source_type,
                risk_type=evidence.risk_type,
                observed_at=evidence.observed_at,
                fact_type=evidence.fact_type,
                fact_key=evidence.fact_key,
                fact_value=evidence.fact_value,
                polarity=evidence.polarity,
                content=evidence.content,
                content_hash=digest,
                evidence_content_hash=digest,
                chunk_schema_version="legacy-stage3",
            )
            session.add(chunk)
            await session.flush()
            chunk_id = chunk.id
            session.add(
                RetrievedEvidence(
                    run_id=run_id, risk_type="judicial", chunk_id=chunk.id, rank=1
                )
            )
        if marker:
            session.add(WorkflowStep(run_id=run_id, name="retrieve_evidence"))
        if manifest:
            rows = [("judicial", chunk_id, 1)] if chunk_id is not None else []
            session.add(WorkflowStep(run_id=run_id, name=_retrieval_manifest(rows)))
    _, headers = await identity("analyst")
    with pytest.raises(RetrievalIntegrityError, match="Partial frozen retrieval"):
        await client.get(f"/investigations/{investigation_id}", headers=headers)


def test_rule_reference_targets_are_version_bound_deterministic_and_nullable() -> None:
    rule_id = uuid4()
    evidence_id = uuid4()
    matching_hash = "a" * 64
    first_id, second_id = uuid4(), uuid4()
    reference = RuleFindingEvidence(
        rule_finding_id=rule_id,
        evidence_id=evidence_id,
        content_hash=matching_hash,
    )
    stale = RuleFindingEvidence(
        rule_finding_id=uuid4(),
        evidence_id=evidence_id,
        content_hash="b" * 64,
    )
    chunks = [
        (
            EvidenceChunk(
                id=chunk_id,
                evidence_id=evidence_id,
                evidence_content_hash=matching_hash,
                content_hash=content_hash,
            ),
            RetrievedEvidence(risk_type="judicial", rank=rank, chunk_id=chunk_id),
        )
        for chunk_id, rank, content_hash in (
            (second_id, 2, "2" * 64),
            (first_id, 1, "1" * 64),
        )
    ]
    targets = resolve_rule_evidence_targets([reference, stale], chunks)
    selected = targets[(rule_id, evidence_id)]
    assert selected.chunk_id == first_id
    assert selected.chunk_content_hash == "1" * 64
    assert targets[(stale.rule_finding_id, evidence_id)].chunk_id is None
    assert targets[(stale.rule_finding_id, evidence_id)].chunk_content_hash is None

    precedence_evidence_id = uuid4()
    precedence_rule_id = uuid4()
    precedence_reference = RuleFindingEvidence(
        rule_finding_id=precedence_rule_id,
        evidence_id=precedence_evidence_id,
        content_hash=matching_hash,
    )
    business_id = UUID("00000000-0000-4000-8000-000000000099")
    judicial_id = UUID("00000000-0000-4000-8000-000000000001")
    precedence = resolve_rule_evidence_targets(
        [precedence_reference],
        [
            (
                EvidenceChunk(
                    id=judicial_id,
                    evidence_id=precedence_evidence_id,
                    evidence_content_hash=matching_hash,
                    content_hash="3" * 64,
                ),
                RetrievedEvidence(risk_type="judicial", rank=1, chunk_id=judicial_id),
            ),
            (
                EvidenceChunk(
                    id=business_id,
                    evidence_id=precedence_evidence_id,
                    evidence_content_hash=matching_hash,
                    content_hash="4" * 64,
                ),
                RetrievedEvidence(risk_type="business", rank=5, chunk_id=business_id),
            ),
        ],
    )
    assert (
        precedence[(precedence_rule_id, precedence_evidence_id)].chunk_id == business_id
    )

    tie_evidence_id = uuid4()
    tie_rule_id = uuid4()
    low_id = UUID("00000000-0000-4000-8000-000000000001")
    high_id = UUID("00000000-0000-4000-8000-000000000002")
    tie_reference = RuleFindingEvidence(
        rule_finding_id=tie_rule_id,
        evidence_id=tie_evidence_id,
        content_hash=matching_hash,
    )
    tie = resolve_rule_evidence_targets(
        [tie_reference],
        [
            (
                EvidenceChunk(
                    id=high_id,
                    evidence_id=tie_evidence_id,
                    evidence_content_hash=matching_hash,
                    content_hash="5" * 64,
                ),
                RetrievedEvidence(risk_type="news", rank=1, chunk_id=high_id),
            ),
            (
                EvidenceChunk(
                    id=low_id,
                    evidence_id=tie_evidence_id,
                    evidence_content_hash=matching_hash,
                    content_hash="6" * 64,
                ),
                RetrievedEvidence(risk_type="news", rank=1, chunk_id=low_id),
            ),
        ],
    )
    assert tie[(tie_rule_id, tie_evidence_id)].chunk_id == low_id

    missing_evidence_id = uuid4()
    missing_rule_id = uuid4()
    missing = RuleFindingEvidence(
        rule_finding_id=missing_rule_id,
        evidence_id=missing_evidence_id,
        content_hash=matching_hash,
    )
    missing_target = resolve_rule_evidence_targets([missing], chunks)[
        (missing_rule_id, missing_evidence_id)
    ]
    assert missing_target.chunk_id is None
    assert missing_target.chunk_content_hash is None


async def test_legacy_rule_reference_has_no_guessed_chunk_target(
    client: httpx.AsyncClient,
) -> None:
    investigation_id, run_id = await awaiting_run()
    async with app.state.session_factory.begin() as session:
        investigation = await session.get(Investigation, investigation_id)
        assert investigation is not None
        digest = sha256(b"mutable catalog evidence").hexdigest()
        evidence = Evidence(
            company_id=investigation.company_id,
            source_type="business",
            risk_type="business",
            source_uri=f"synthetic://legacy-reference/{run_id}",
            title="Must not be guessed",
            observed_at=date(2026, 9, 1),
            fact_type="business_abnormal_status",
            fact_key="legacy",
            fact_value="active",
            polarity="negative",
            content="mutable catalog evidence",
            content_hash=digest,
        )
        rule = RuleFinding(
            run_id=run_id,
            rule_code="business",
            rule_version="1",
            score=5,
            parameters={},
        )
        session.add_all([evidence, rule, WorkflowStep(run_id=run_id, name="rule")])
        await session.flush()
        session.add(
            RuleFindingEvidence(
                rule_finding_id=rule.id,
                evidence_id=evidence.id,
                content_hash=evidence.content_hash,
            )
        )
    _, headers = await identity("analyst")
    response = await client.get(f"/investigations/{investigation_id}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["retrieval_state"] == "legacy_unavailable"
    reference = body["rule_findings"][0]["evidence"][0]
    assert reference["chunk_id"] is None
    assert reference["chunk_content_hash"] is None


@pytest.mark.parametrize(
    ("support_status", "support_reason"),
    [
        ("supported", "canonical_fact_match"),
        ("indeterminate", "unverified_free_text_extension"),
    ],
)
async def test_detail_preserves_persisted_support_variants(
    client: httpx.AsyncClient,
    support_status: str,
    support_reason: str,
) -> None:
    investigation_id, run_id = await awaiting_run()
    await add_finding(
        run_id,
        support_status=support_status,
        support_reason=support_reason,
    )
    _, headers = await identity("analyst")
    response = await client.get(f"/investigations/{investigation_id}", headers=headers)
    assert response.status_code == 200
    finding = response.json()["ai_findings"][0]
    assert finding["support_status"] == support_status
    assert finding["support_reason"] == support_reason
    assert finding["citations"] == [AI_CITATION]


async def test_detail_rejects_unknown_persisted_support(
    client: httpx.AsyncClient,
) -> None:
    investigation_id, run_id = await awaiting_run()
    await add_finding(run_id, support_reason="invented")
    _, headers = await identity("analyst")
    with pytest.raises(ValidationError):
        await client.get(f"/investigations/{investigation_id}", headers=headers)


async def test_detail_returns_complete_conflict_contract(
    client: httpx.AsyncClient,
) -> None:
    investigation_id, run_id = await awaiting_run()
    async with app.state.session_factory.begin() as session:
        investigation = await session.get(Investigation, investigation_id)
        assert investigation is not None
        chunks: list[EvidenceChunk] = []
        for source_type, fact_value in (("judicial", "open"), ("news", "closed")):
            content = f"{source_type}:{fact_value}"
            digest = sha256(content.encode()).hexdigest()
            evidence = Evidence(
                company_id=investigation.company_id,
                source_type=source_type,
                risk_type="judicial",
                source_uri=f"synthetic://conflict/{run_id}/{source_type}",
                title=f"{source_type} side",
                observed_at=date(2026, 9, 1),
                fact_type="judicial_case_status",
                fact_key="case-1",
                fact_value=fact_value,
                polarity="negative",
                content=content,
                content_hash=digest,
            )
            session.add(evidence)
            await session.flush()
            chunk = EvidenceChunk(
                evidence_id=evidence.id,
                company_id=evidence.company_id,
                source_type=evidence.source_type,
                risk_type=evidence.risk_type,
                observed_at=evidence.observed_at,
                fact_type=evidence.fact_type,
                fact_key=evidence.fact_key,
                fact_value=evidence.fact_value,
                polarity=evidence.polarity,
                content=evidence.content,
                content_hash=digest,
                evidence_content_hash=digest,
                chunk_schema_version="legacy-stage3",
            )
            session.add(chunk)
            await session.flush()
            chunks.append(chunk)
        session.add_all(
            [
                WorkflowStep(run_id=run_id, name="retrieve_evidence"),
                WorkflowStep(
                    run_id=run_id,
                    name=_retrieval_manifest(
                        [
                            ("judicial", chunks[0].id, 1),
                            ("judicial", chunks[1].id, 2),
                        ]
                    ),
                ),
                RetrievedEvidence(
                    run_id=run_id,
                    risk_type="judicial",
                    chunk_id=chunks[0].id,
                    rank=1,
                ),
                RetrievedEvidence(
                    run_id=run_id,
                    risk_type="judicial",
                    chunk_id=chunks[1].id,
                    rank=2,
                ),
            ]
        )
    _, headers = await identity("analyst")
    response = await client.get(f"/investigations/{investigation_id}", headers=headers)
    assert response.status_code == 200
    conflict = response.json()["conflicts"][0]
    assert conflict["authority_source"] == "judicial"
    assert conflict["resolved_value"] == "open"
    assert conflict["resolution_state"] == "resolved"
    assert [
        (side["source_type"], side["fact_value"]) for side in conflict["sides"]
    ] == [("judicial", "open"), ("news", "closed")]


async def test_run_review_guards_bounds_and_exact_replay(
    client: httpx.AsyncClient,
) -> None:
    _, run_id = await awaiting_run()
    assert (await review(client, run_id, {})).status_code == 401
    _, analyst = await identity("analyst")
    assert (await review(client, run_id, analyst)).status_code == 403
    reviewer_id, headers = await identity("reviewer")
    for changes in (
        {"finding_version": 1},
        {"reviewer_id": str(uuid4())},
        {"reason": " "},
        {"reason": ""},
        {"reason": "x" * 2001},
        {"decision": "edit"},
    ):
        assert (await review(client, run_id, headers, **changes)).status_code == 422
    for key in ("", "x" * 201):
        assert (await review(client, run_id, headers, key)).status_code == 422
    assert (
        await client.post(
            f"/runs/{run_id}/review",
            headers=headers,
            json={"decision": "approve", "reason": "OK"},
        )
    ).status_code == 422
    assert (await review(client, uuid4(), headers)).status_code == 409
    for body in ({"decision": "approve"}, {"reason": "OK"}):
        assert (
            await client.post(
                f"/runs/{run_id}/review",
                headers={**headers, "Idempotency-Key": "missing-body"},
                json=body,
            )
        ).status_code == 422
    _, self_run = await awaiting_run(owner=reviewer_id)
    assert (await review(client, self_run, headers)).status_code == 409
    _, ai_run = await awaiting_run()
    await add_finding(ai_run)
    assert (await review(client, ai_run, headers)).status_code == 409
    investigation_id, old_run = await awaiting_run()
    async with app.state.session_factory.begin() as session:
        investigation = await session.get(Investigation, investigation_id)
        assert investigation is not None
        new_run = WorkflowRun(investigation_id=investigation_id, run_number=2)
        session.add(new_run)
        await session.flush()
        investigation.current_run_id = new_run.id
    assert (await review(client, old_run, headers)).status_code == 409
    assert (await review(client, new_run.id, headers)).status_code == 409
    first = await review(client, run_id, headers)
    assert first.status_code == 200
    assert (await review(client, run_id, headers)).json() == first.json()
    for changes in ({"reason": "Different"}, {"decision": "reject"}):
        assert (await review(client, run_id, headers, **changes)).status_code == 409
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "resume_queued"
        run.status = "completed"
        decision = await session.scalar(select(ReviewDecision))
        assert decision is not None and decision.reviewer_id == reviewer_id
        assert decision.ai_finding_id is None and decision.finding_version is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(DispatchOutbox.operation == "resume")
            )
            == 1
        )
    assert (await review(client, run_id, headers)).json() == first.json()
    _, admin = await identity("admin")
    _, admin_run = await awaiting_run()
    assert (await review(client, admin_run, admin)).status_code == 200
