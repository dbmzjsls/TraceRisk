import asyncio
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.test_review_api import identity, post
from tests.integration.test_stage4_workflow_retrieval import seed
from tracerisk.ai.claims import ClaimFact, canonical_statement
from tracerisk.ai.models import AIFinding
from tracerisk.ai.service import generate_validate_findings
from tracerisk.audit.models import AuditEvent
from tracerisk.config import Settings
from tracerisk.evidence.models import EvidenceChunk, RetrievedEvidence
from tracerisk.evidence.service import retrieve_evidence
from tracerisk.investigations.models import DispatchOutbox, WorkflowRun
from tracerisk.main import app
from tracerisk.reports.models import RiskReport
from tracerisk.reports.service import generate_report
from tracerisk.reviews.models import HumanFindingVersion, ReviewDecision
from tracerisk.rules.models import RuleFinding

pytestmark = pytest.mark.anyio
pytest_plugins = ["tests.integration.test_review_api"]


async def review_context() -> tuple[UUID, list[UUID], dict[str, Any], dict[str, Any]]:
    sessions = app.state.session_factory
    async with sessions.begin() as session:
        run_id, _ = await seed(session)
        await retrieve_evidence(session, run_id=run_id)
    await generate_validate_findings(sessions, run_id=run_id)
    async with sessions.begin() as session:
        finding = await session.scalar(
            select(AIFinding).where(AIFinding.run_id == run_id)
        )
        assert finding is not None
        original = {
            c.name: deepcopy(getattr(finding, c.name))
            for c in finding.__table__.columns
        }
        chunk = await session.get(
            EvidenceChunk, UUID(str(finding.citations[0]["chunk_id"]))
        )
        assert chunk is not None
        wording = canonical_statement(
            ClaimFact(
                risk_type=chunk.risk_type,
                source_type=chunk.source_type,
                fact_type=chunk.fact_type,
                fact_key=chunk.fact_key,
                fact_value=chunk.fact_value,
                polarity=chunk.polarity,
            )
        )
        replacement = dict(
            finding_key=finding.finding_key,
            risk_type=finding.risk_type,
            claim_code=finding.claim_code,
            title=wording,
            summary=wording,
            reasoning=wording,
            evidence_citations=[deepcopy(finding.citations[0])],
            evidence_sufficiency="sufficient",
            confidence_level="medium",
            review_recommended=False,
        )
        second = AIFinding(**{k: v for k, v in original.items() if k != "id"})
        second.finding_key = "second"
        session.add(second)
        session.add(
            RuleFinding(
                run_id=run_id,
                rule_code="judicial",
                rule_version="1",
                score=35,
                parameters={},
            )
        )
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.status = "awaiting_review"
        await session.flush()
        return run_id, [finding.id, second.id], replacement, original


async def modify(
    client: httpx.AsyncClient,
    finding_id: UUID,
    headers: dict[str, str],
    replacement: dict[str, Any],
    key: str = "modify",
) -> httpx.Response:
    return await post(
        client, finding_id, headers, key, decision="modify", replacement=replacement
    )


async def test_modify_persists_validated_version_replays_and_selects_final_report(
    client: httpx.AsyncClient,
) -> None:
    reviewer_id, headers = await identity("reviewer")
    run_id, ids, replacement, original = await review_context()
    responses = await asyncio.gather(
        modify(client, ids[0], headers, replacement),
        modify(client, ids[0], headers, replacement),
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    decision_id = UUID(responses[0].json()["decision_id"])
    assert (
        await modify(client, ids[0], headers, {**replacement, "summary": "changed"})
    ).status_code == 409
    assert (
        await modify(client, ids[0], headers, replacement, "duplicate")
    ).status_code == 409
    async with app.state.session_factory() as session:
        ai = await session.get(AIFinding, ids[0])
        assert ai is not None
        assert {c.name: getattr(ai, c.name) for c in ai.__table__.columns} == original
        human = await session.scalar(select(HumanFindingVersion))
        assert human is not None
        assert (
            human.run_id,
            human.ai_finding_id,
            human.finding_version,
            human.reviewer_id,
            human.review_decision_id,
            human.version,
        ) == (run_id, ids[0], 1, reviewer_id, decision_id, 1)
        assert human.submitted_replacement == replacement
        assert human.replacement["support_status"] == "supported"
        assert human.replacement["support_reason"] == "canonical_fact_match"
        human_id = human.id
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "awaiting_review"
        investigation_id = run.investigation_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(DispatchOutbox.operation == "resume")
            )
            == 0
        )
    detail = await client.get(f"/investigations/{investigation_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["ai_findings"][0]["finding_key"] == replacement["finding_key"]
    assert detail.json()["decisions"][0]["decision"] == "modify"
    assert detail.json()["decisions"][0]["human_finding_version_id"] == str(human_id)
    assert detail.json()["decisions"][0]["replacement"]["title"] == replacement["title"]
    assert (await post(client, ids[1], headers, "approve-second")).status_code == 200
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "resume_queued"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(DispatchOutbox.operation == "resume")
            )
            == 1
        )
        run.status = "running"
    report_id = await generate_report(app.state.session_factory, run_id, decision_id)
    async with app.state.session_factory() as session:
        report = await session.get(RiskReport, report_id)
        assert report is not None
        snapshot = report.snapshot
        assert snapshot["snapshot_version"] == "stage6-v1"
        assert snapshot["rule_score"] == 35
        ai_items = snapshot["ai_findings"]
        final_items = snapshot["final_findings"]
        human_decisions = snapshot["human_decisions"]
        assert (
            isinstance(ai_items, list)
            and isinstance(final_items, list)
            and isinstance(human_decisions, list)
        )
        assert (
            next(f for f in ai_items if f["id"] == str(ids[0]))["title"]
            == original["title"]
        )
        final = next(
            f for f in final_items if f["original_ai_finding_id"] == str(ids[0])
        )
        assert final["id"] == str(human_id)
        assert final["source"] == "human" and final["human_version"] == 1
        assert (
            final["title"] == replacement["title"]
            and final["citations"] == replacement["evidence_citations"]
        )
        assert len(final_items) == 2
        decision = next(d for d in human_decisions if d["id"] == str(decision_id))
        assert decision["human_finding_version_id"] == str(human_id)
        assert decision["replacement"]["title"] == replacement["title"]
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.resource_id == decision_id)
        )
        assert audit is not None and audit.actor_id == reviewer_id
    assert (await modify(client, ids[0], headers, replacement)).json() == responses[
        0
    ].json()
    assert (
        await generate_report(app.state.session_factory, run_id, decision_id)
        == report_id
    )


async def test_invalid_modified_citations_and_fields_leave_no_partial_writes(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await identity("reviewer")
    run_id, ids, replacement, _ = await review_context()
    async with app.state.session_factory() as session:
        outside = await session.scalar(
            select(EvidenceChunk).where(
                EvidenceChunk.risk_type == "judicial",
                ~select(RetrievedEvidence.chunk_id)
                .where(
                    RetrievedEvidence.run_id == run_id,
                    RetrievedEvidence.chunk_id == EvidenceChunk.id,
                )
                .exists(),
            )
        )
        assert outside is not None
        outside_citation = dict(
            evidence_id=str(outside.evidence_id),
            chunk_id=str(outside.id),
            content_hash=outside.content_hash,
            quoted_excerpt=outside.content,
        )
    citation = replacement["evidence_citations"][0]
    invalid = [
        {**replacement, "evidence_citations": [outside_citation]},
        *[
            {**replacement, "evidence_citations": [{**citation, **change}]}
            for change in (
                {"chunk_id": str(uuid4())},
                {"evidence_id": str(uuid4())},
                {"content_hash": "a" * 64},
                {"quoted_excerpt": "invented text"},
            )
        ],
        {**replacement, "claim_code": "JUDICIAL_CLOSED_CASE"},
        {**replacement, "claim_code": "UNKNOWN"},
        {**replacement, "risk_type": "news"},
        {**replacement, "finding_key": "other"},
        {**replacement, "score": 0},
        {**replacement, "support_status": "supported"},
    ]
    for index, data in enumerate(invalid):
        assert (
            await modify(client, ids[0], headers, data, f"invalid-{index}")
        ).status_code == 422
    assert (await post(client, ids[0], headers, decision="modify")).status_code == 422
    assert (
        await post(client, ids[0], headers, replacement=replacement)
    ).status_code == 422
    async with app.state.session_factory() as session:
        for model in (ReviewDecision, HumanFindingVersion):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "review.submitted")
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(DispatchOutbox.operation == "resume")
            )
            == 0
        )
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "awaiting_review"


async def test_human_support_downgrade_and_findings_are_immutable(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await identity("reviewer")
    _, ids, replacement, _ = await review_context()
    replacement["summary"] = "A human interpretation beyond the canonical fact"
    assert (await modify(client, ids[0], headers, replacement)).status_code == 200
    async with app.state.session_factory() as session:
        human = await session.scalar(select(HumanFindingVersion))
        assert human is not None
        assert human.replacement["support_status"] == "indeterminate"
        assert human.replacement["support_reason"] == "unverified_free_text_extension"
        assert human.replacement["evidence_sufficiency"] == "insufficient"
        assert human.replacement["review_recommended"] is True
    for model in (AIFinding, HumanFindingVersion):
        for statement in (update(model).values(version=2), delete(model)):
            with pytest.raises(IntegrityError, match="immutable"):
                async with app.state.session_factory.begin() as session:
                    await session.execute(statement)


def test_human_version_migration_can_downgrade_and_upgrade(database: Settings) -> None:
    config = Config("alembic.ini")
    command.downgrade(config, "0011")
    command.upgrade(config, "head")


@pytest.mark.parametrize("decision", ["modify", "approve"])
async def test_downgrade_preserves_human_audit_even_if_decision_is_rewritten(
    client: httpx.AsyncClient, decision: str
) -> None:
    _, headers = await identity("reviewer")
    _, ids, replacement, _ = await review_context()
    response = await modify(client, ids[0], headers, replacement)
    assert response.status_code == 200
    decision_id = UUID(response.json()["decision_id"])
    async with app.state.session_factory.begin() as session:
        await session.execute(
            update(ReviewDecision)
            .where(ReviewDecision.id == decision_id)
            .values(decision=decision)
        )
        human = await session.scalar(select(HumanFindingVersion))
        assert human is not None
        original = {
            column.name: deepcopy(getattr(human, column.name))
            for column in human.__table__.columns
        }
    with pytest.raises(DBAPIError, match="Cannot downgrade"):
        command.downgrade(Config("alembic.ini"), "0011")
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0012"
        )
        assert (
            await session.scalar(text("SELECT to_regclass('human_finding_versions')"))
            == "human_finding_versions"
        )
        persisted = await session.get(HumanFindingVersion, original["id"])
        assert persisted is not None
        assert {
            column.name: getattr(persisted, column.name)
            for column in persisted.__table__.columns
        } == original
        assert (
            await session.scalar(
                select(ReviewDecision.decision).where(ReviewDecision.id == decision_id)
            )
            == decision
        )


async def test_concurrent_different_modifications_and_resume_rollback(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await identity("reviewer")
    run_id, ids, replacement, _ = await review_context()
    responses = await asyncio.gather(
        modify(client, ids[0], headers, replacement, "one"),
        modify(
            client, ids[0], headers, {**replacement, "confidence_level": "low"}, "two"
        ),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with app.state.session_factory.begin() as session:
        session.add(DispatchOutbox(run_id=run_id, operation="resume"))
    second_replacement = {**replacement, "finding_key": "second"}
    assert (
        await modify(client, ids[1], headers, second_replacement, "rollback")
    ).status_code == 409
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(HumanFindingVersion))
            == 1
        )
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "awaiting_review"
