import asyncio
from collections.abc import AsyncIterator
from datetime import date
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
from tracerisk.investigations.models import DispatchOutbox, Investigation, WorkflowRun
from tracerisk.main import app
from tracerisk.reviews.models import ReviewDecision
from tracerisk.reviews.service import ReviewConflict, RunReview, submit_review

SECRET = "review-integration-test-secret-at-least-48-characters"
AI_CITATION = {
    "evidence_id": "21000000-0000-0000-0000-000000000001",
    "chunk_id": "00000000-0000-4000-8000-000000000006",
    "content_hash": "c" * 64,
    "quoted_excerpt": "Exact persisted excerpt",
}


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


async def findings(
    count: int = 1,
    owner: UUID | None = None,
    *,
    support_status: str = "indeterminate",
    support_reason: str = "legacy-stage3",
) -> tuple[UUID, list[UUID]]:
    run_id, investigation_id = uuid4(), uuid4()
    async with app.state.session_factory.begin() as session:
        company = Company(
            synthetic_key=str(uuid4()),
            name="Review company",
            unified_credit_code=str(uuid4()),
        )
        session.add(company)
        await session.flush()
        session.add(
            Investigation(
                id=investigation_id,
                company_id=company.id,
                created_by=owner or uuid4(),
                as_of_date=date(2026, 9, 11),
                current_run_id=run_id,
            )
        )
        session.add(
            WorkflowRun(
                id=run_id,
                investigation_id=investigation_id,
                status="awaiting_review",
            )
        )
        await session.flush()
        ids = []
        for index in range(count):
            finding = AIFinding(
                run_id=run_id,
                finding_key=f"judicial:{index}",
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
            ids.append(finding.id)
        return run_id, ids


async def post(
    client: httpx.AsyncClient,
    finding: UUID,
    headers: dict[str, str],
    key: str = "review-1",
    **changes: object,
) -> httpx.Response:
    return await client.post(
        f"/reviews/{finding}",
        headers={**headers, "Idempotency-Key": key},
        json={
            "decision": "approve",
            "finding_version": 1,
            "reason": "Checked",
            **changes,
        },
    )


@pytest.mark.anyio
async def test_roles_and_bounded_input(client: httpx.AsyncClient) -> None:
    _, ids = await findings()
    assert (await client.get("/reviews/pending")).status_code == 401
    assert (await post(client, ids[0], {})).status_code == 401
    _, analyst = await identity("analyst")
    assert (await client.get("/reviews/pending", headers=analyst)).status_code == 403
    assert (await post(client, ids[0], analyst)).status_code == 403
    for name in ("reviewer", "admin"):
        _, headers = await identity(name)
        response = await client.get("/reviews/pending", headers=headers)
        assert response.status_code == 200
        assert response.json()[0]["finding_id"] == str(ids[0])
        assert response.json()[0]["support_status"] == "indeterminate"
        assert response.json()[0]["support_reason"] == "legacy-stage3"
        assert response.json()[0]["citations"] == [AI_CITATION]
        assert "password_hash" not in response.text
    _, reviewer = await identity("reviewer")
    for changes in (
        {"reason": " "},
        {"reason": "x" * 2001},
        {"finding_version": 0},
        {"decision": "edit"},
        {"reviewer_id": str(uuid4())},
    ):
        assert (await post(client, ids[0], reviewer, **changes)).status_code == 422
    for key in ("", "x" * 201):
        assert (await post(client, ids[0], reviewer, key)).status_code == 422
    response = await client.post(
        f"/reviews/{ids[0]}",
        headers=reviewer,
        json={"decision": "approve", "finding_version": 1, "reason": "OK"},
    )
    assert response.status_code == 422
    _, admin = await identity("admin")
    assert (await post(client, ids[0], admin)).status_code == 200


@pytest.mark.anyio
async def test_pending_rejects_unknown_persisted_support(
    client: httpx.AsyncClient,
) -> None:
    await findings(support_status="invented")
    _, headers = await identity("reviewer")
    with pytest.raises(ValidationError):
        await client.get("/reviews/pending", headers=headers)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("support_status", "support_reason"),
    [
        ("supported", "canonical_fact_match"),
        ("indeterminate", "unverified_free_text_extension"),
    ],
)
async def test_pending_preserves_persisted_support_variants_and_citations(
    client: httpx.AsyncClient,
    support_status: str,
    support_reason: str,
) -> None:
    await findings(support_status=support_status, support_reason=support_reason)
    _, headers = await identity("reviewer")
    response = await client.get("/reviews/pending", headers=headers)
    assert response.status_code == 200
    finding_body = response.json()[0]
    assert finding_body["support_status"] == support_status
    assert finding_body["support_reason"] == support_reason
    assert finding_body["citations"] == [AI_CITATION]


@pytest.mark.anyio
async def test_guards_and_legacy_cannot_bypass(client: httpx.AsyncClient) -> None:
    reviewer_id, headers = await identity("reviewer")
    _, self_ids = await findings(owner=reviewer_id)
    assert (await post(client, self_ids[0], headers)).status_code == 409
    run_id, ids = await findings()
    assert (await post(client, ids[0], headers, finding_version=2)).status_code == 409
    assert (await post(client, uuid4(), headers)).status_code == 409
    with pytest.raises(ReviewConflict, match="finding-level"):
        await submit_review(
            app.state.session_factory,
            RunReview(
                run_id=run_id,
                reviewer_id=reviewer_id,
                idempotency_key="legacy",
                decision="approve",
                reason="Checked",
            ),
        )
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "running"
    assert (await post(client, ids[0], headers)).status_code == 409
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "awaiting_review"
        investigation = await session.get(Investigation, run.investigation_id)
        assert investigation is not None
        replacement = WorkflowRun(investigation_id=investigation.id, run_number=2)
        session.add(replacement)
        await session.flush()
        investigation.current_run_id = replacement.id
    assert (await post(client, ids[0], headers)).status_code == 409


@pytest.mark.anyio
async def test_barrier_and_exact_replay_after_completion(
    client: httpx.AsyncClient,
) -> None:
    reviewer_id, headers = await identity("reviewer")
    run_id, ids = await findings(2)
    first = await post(client, ids[0], headers)
    assert first.status_code == 200
    assert (await post(client, ids[0], headers)).json() == first.json()
    async with app.state.session_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "awaiting_review"
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 0
        )
    for changes in (
        {"decision": "reject"},
        {"reason": "Different"},
        {"finding_version": 2},
    ):
        assert (await post(client, ids[0], headers, **changes)).status_code == 409
    assert (await post(client, ids[1], headers)).status_code == 409
    _, other_ids = await findings()
    assert (await post(client, other_ids[0], headers)).status_code == 409
    assert (
        await post(client, ids[1], headers, "review-2", decision="reject")
    ).status_code == 200
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "resume_queued"
        run.status = "completed"
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 2
        )
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 1
        )
        assert (
            await session.scalar(select(ReviewDecision.reviewer_id).limit(1))
            == reviewer_id
        )
    assert (await post(client, ids[0], headers)).json() == first.json()
    assert (await client.get("/reviews/pending", headers=headers)).json()[0][
        "finding_id"
    ] == str(other_ids[0])


@pytest.mark.anyio
async def test_concurrent_findings_and_transaction_rollback(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await identity("reviewer")
    run_id, ids = await findings(3)
    responses = await asyncio.gather(
        *[
            post(client, finding, headers, f"concurrent-{index}")
            for index, finding in enumerate(ids)
        ]
    )
    assert [response.status_code for response in responses] == [200, 200, 200]
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 3
        )
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 1
        )
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "resume_queued"
    rollback_run, rollback_ids = await findings()
    async with app.state.session_factory.begin() as session:
        session.add(DispatchOutbox(run_id=rollback_run, operation="resume"))
    assert (await post(client, rollback_ids[0], headers, "rollback")).status_code == 409
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 3
        )
        run = await session.get(WorkflowRun, rollback_run)
        assert run is not None and run.status == "awaiting_review"


@pytest.mark.anyio
async def test_concurrent_same_key_and_current_version_barrier(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await identity("reviewer")
    run_id, ids = await findings(2)
    responses = await asyncio.gather(
        post(client, ids[0], headers, "same-key"),
        post(client, ids[0], headers, "same-key"),
    )
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert (
        await post(client, ids[1], headers, "wrong-version", finding_version=2)
    ).status_code == 409
    async with app.state.session_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "awaiting_review"
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 0
        )
    assert (await post(client, ids[1], headers, "other-key")).status_code == 200
    async with app.state.session_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "resume_queued"
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 2
        )
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 1
        )
    pending = await client.get("/reviews/pending", headers=headers)
    assert pending.json() == []
