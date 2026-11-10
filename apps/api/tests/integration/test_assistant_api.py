import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from tracerisk.ai.models import AIFinding, ModelInvocation
from tracerisk.ai.service import generate_validate_findings
from tracerisk.assistant import model, service
from tracerisk.assistant.schemas import AssistantSelection
from tracerisk.auth.models import User
from tracerisk.auth.seed import seed_accounts
from tracerisk.auth.service import create_access_token
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence
from tracerisk.evidence.service import retrieve_evidence
from tracerisk.investigations.models import Investigation, WorkflowRun
from tracerisk.investigations.service import create_investigation
from tracerisk.main import app
from tracerisk.reports.models import RiskReport
from tracerisk.reports.service import generate_report
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews.models import HumanFindingVersion, ReviewDecision
from tracerisk.rules.engine import evaluate_all_rules, save_rule_findings
from tracerisk.rules.judicial import JUDICIAL_RULE_CODE
from tracerisk.rules.models import RuleFinding
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog

pytestmark = pytest.mark.anyio
SECRET = "assistant-api-test-secret-at-least-48-characters-long"
BASELINE = date(2026, 9, 11)


@dataclass(frozen=True)
class Case:
    investigation_id: UUID
    run_id: UUID


@pytest.fixture
async def client(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("MODEL_ADAPTER", "mock")
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


@pytest.fixture
async def cases(client: httpx.AsyncClient) -> dict[str, Case]:
    owner, _ = await identity("analyst")
    scenarios = tuple(
        scenario
        for scenario in generate_catalog()
        if scenario.company.unified_credit_code
        in {
            "SYNTHETIC-COMPANY-002",
            "SYNTHETIC-COMPANY-003",
            "SYNTHETIC-COMPANY-007",
        }
    )
    result = {}
    sessions = app.state.session_factory
    async with sessions.begin() as session:
        await import_catalog(session, scenarios)
        await build_index(session, FakeEmbedding(), IndexContract.fake())
        for scenario in scenarios:
            company_id = scenario.company.id
            investigation = await create_investigation(
                session, company_id=company_id, created_by=owner, as_of_date=BASELINE
            )
            run = await session.get(WorkflowRun, investigation.current_run_id)
            assert run is not None
            run.status = "running"
            result[scenario.company.unified_credit_code[-3:]] = Case(
                investigation.id, run.id
            )
    for case in result.values():
        async with sessions.begin() as session:
            await retrieve_evidence(
                session, run_id=case.run_id, contract=IndexContract.fake()
            )
            investigation = await session.get(Investigation, case.investigation_id)
            assert investigation is not None
            score = await evaluate_all_rules(
                session,
                company_id=investigation.company_id,
                as_of_date=BASELINE,
            )
            await save_rule_findings(session, run_id=case.run_id, score=score)
        await generate_validate_findings(sessions, run_id=case.run_id)
        async with sessions.begin() as session:
            run = await session.get(WorkflowRun, case.run_id)
            assert run is not None
            run.status = "awaiting_review"
    return result


async def ask(
    client: httpx.AsyncClient, case: Case, question: str, **overrides: object
) -> httpx.Response:
    _, headers = await identity("analyst")
    return await client.post(
        "/assistant/answers",
        headers=headers,
        json={
            "investigation_id": str(case.investigation_id),
            "run_id": str(case.run_id),
            "question": question,
            **overrides,
        },
    )


def kind(body: dict[str, Any], section_kind: str) -> dict[str, Any]:
    return next(
        section for section in body["sections"] if section["kind"] == section_kind
    )


async def persisted_state() -> tuple[object, ...]:
    async with app.state.session_factory() as session:
        return (
            tuple(
                (row.id, row.score)
                for row in (
                    await session.scalars(select(RuleFinding).order_by(RuleFinding.id))
                )
            ),
            await session.scalar(select(func.count()).select_from(ModelInvocation)),
            await session.scalar(select(func.count()).select_from(ReviewDecision)),
            tuple(
                (
                    report.id,
                    sha256(
                        json.dumps(report.snapshot, sort_keys=True).encode()
                    ).hexdigest(),
                )
                for report in (
                    await session.scalars(select(RiskReport).order_by(RiskReport.id))
                )
            ),
            await session.scalar(select(func.count()).select_from(WorkflowRun)),
        )


async def test_questions_change_sections_and_keep_frozen_citations(
    client: httpx.AsyncClient, cases: dict[str, Case]
) -> None:
    before = await persisted_state()
    broad_question = (
        "请综合调查经营、司法、舆情和知识产权，并展示证据和需要人工核查的事项。"
    )
    broad = await ask(client, cases["003"], broad_question)
    assert broad.status_code == 200, broad.text
    body = broad.json()
    assert body["rule_score"] == 95 and body["run_status"] == "awaiting_review"
    assert body["investigation_model_execution"]["mode"] == "mock"
    assert body["assistant_execution"]["mode"] == "mock"
    assert body["question"] == broad_question
    assert body["question_sha256"] == sha256(broad_question.encode()).hexdigest()
    assert "待人工复核" in broad.text
    assert {item["risk_type"] for item in body["citations"]} == {
        "business",
        "judicial",
        "news",
        "intellectual_property",
    }
    assert len(kind(body, "rules")["lines"]) == 4
    assert kind(body, "findings")["findings"][0]["source"] == "pending_ai"

    coverage = await ask(
        client, cases["007"], "若规则分为0，缺失或不可用的来源有哪些？"
    )
    assert coverage.status_code == 200, coverage.text
    coverage_body = coverage.json()
    assert coverage_body["rule_score"] == 0
    assert "工商来源不可用" in "\n".join(kind(coverage_body, "coverage")["lines"])
    assert "0分不代表安全" in coverage.text
    assert "待人工复核" in coverage.text
    assert coverage_body["citations"] == []

    followup = await ask(client, cases["003"], "只看司法证据，解释司法规则为什么计35分")
    assert followup.status_code == 200, followup.text
    narrowed = followup.json()
    assert narrowed["run_id"] == body["run_id"]
    assert narrowed["context_sha256"] == body["context_sha256"]
    assert narrowed["question_sha256"] != body["question_sha256"]
    assert len(kind(narrowed, "rules")["lines"]) == 1
    assert "35分" in kind(narrowed, "rules")["lines"][0]
    assert {item["risk_type"] for item in narrowed["citations"]} == {"judicial"}
    assert {section["kind"] for section in narrowed["sections"]} != {
        section["kind"] for section in body["sections"]
    }
    for item in narrowed["citations"]:
        assert item in body["citations"]
        assert sha256(item["content"].encode()).hexdigest() == item["content_hash"]
        assert item["source_uri"] and item["observed_at"] <= str(BASELINE)
    same_run_coverage = await ask(
        client, cases["003"], "本次调查有哪些缺失或不可用的来源？"
    )
    assert same_run_coverage.status_code == 200, same_run_coverage.text
    assert same_run_coverage.json()["context_sha256"] == body["context_sha256"]
    assert same_run_coverage.json()["citations"] == []
    assert "rules" not in {
        section["kind"] for section in same_run_coverage.json()["sections"]
    }
    unsupported = await ask(client, cases["003"], "能否直接给出贷款额度？")
    assert unsupported.status_code == 200
    assert unsupported.json()["status"] == "unsupported"
    assert unsupported.json()["reason_code"] == "outside_scope"
    assert await persisted_state() == before


async def test_coverage_discloses_absent_findings_even_when_model_omits_section(
    client: httpx.AsyncClient, cases: dict[str, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def coverage_only(*args: Any) -> Any:
        _, execution = await model.select_answer(*args)
        return AssistantSelection(
            status="answer",
            sections=["coverage"],
            rule_codes=[],
            finding_keys=[],
            chunk_ids=[],
            reason_code="none",
        ), execution

    monkeypatch.setattr(service, "select_answer", coverage_only)
    answer = await ask(client, cases["007"], "缺失或不可用的来源有哪些？")
    assert answer.status_code == 200
    assert "本次运行没有待审AI发现。" in kind(answer.json(), "coverage")["lines"]
    assert "待人工复核" in answer.text


async def test_mock_clarifies_conflicting_scope_and_contextless_questions(
    client: httpx.AsyncClient, cases: dict[str, Case]
) -> None:
    case = cases["003"]
    for question, status, reason in (
        ("请综合调查截至2025年1月1日的结果", "clarify", "scope_mismatch"),
        ("请说明以 2025-01-01 为基准日的结果", "clarify", "scope_mismatch"),
        ("请调查澄明设备（合成）有限公司的司法风险", "clarify", "scope_mismatch"),
        ("澄明设备（合成）有限公司的工商规则是什么？", "clarify", "scope_mismatch"),
        ("请调查 SYNTHETIC-COMPANY-002 的风险", "clarify", "scope_mismatch"),
        ("明天天气怎么样？", "unsupported", "outside_scope"),
        ("你好", "clarify", "no_context"),
        ("继续", "clarify", "no_context"),
    ):
        response = await ask(client, case, question)
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["status"], result["reason_code"]) == (status, reason), question
        assert result["citations"] == []
        assert not any(section["findings"] for section in result["sections"])
    for question in (
        "请调查远景精工（合成）有限公司截至2026年9月11日的司法风险",
        "请说明 SYNTHETIC-COMPANY-003 的司法规则",
        "2025年1月1日立案的案件是否被本次司法证据覆盖？",
        "为什么计35分？请看司法规则。",
    ):
        response = await ask(client, case, question)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "answer", question
        assert {item["risk_type"] for item in result["citations"]} == {"judicial"}


async def test_wrong_run_pending_run_authentication_and_request_shape(
    client: httpx.AsyncClient, cases: dict[str, Case]
) -> None:
    case = cases["003"]
    assert (
        await client.post(
            "/assistant/answers",
            json={
                "investigation_id": str(case.investigation_id),
                "run_id": str(case.run_id),
                "question": "请调查",
            },
        )
    ).status_code == 401
    assert (
        await ask(client, case, "综合", run_id=str(cases["007"].run_id))
    ).status_code == 409
    assert (
        await ask(client, case, "综合", investigation_id=str(uuid4()))
    ).status_code == 404
    assert (await ask(client, case, "综合", rule_score=95)).status_code == 422
    assert (await ask(client, case, " ")).status_code == 422
    assert (await ask(client, case, "问" * 2001)).status_code == 422
    async with app.state.session_factory.begin() as session:
        run = await session.get(WorkflowRun, case.run_id)
        assert run is not None
        run.status = "running"
    assert (await ask(client, case, "综合")).status_code == 409


async def test_invalid_model_selection_and_provider_failure_are_explicit(
    client: httpx.AsyncClient, cases: dict[str, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, headers = await identity("analyst")
    case = cases["003"]
    detail = (
        await client.get(f"/investigations/{case.investigation_id}", headers=headers)
    ).json()
    business_chunk = next(
        item["chunk_id"]
        for item in detail["retrieved_evidence"]
        if item["risk_type"] == "business"
    )
    other = (
        await client.get(
            f"/investigations/{cases['002'].investigation_id}", headers=headers
        )
    ).json()
    cross_run_chunk = other["retrieved_evidence"][0]["chunk_id"]
    before = await persisted_state()
    for chunk_id in (str(uuid4()), business_chunk, cross_run_chunk):

        async def bad_selector(*args: Any, invalid_chunk: str = chunk_id) -> Any:
            _, execution = await model.select_answer(*args)
            return AssistantSelection(
                status="answer",
                sections=["rules", "evidence"],
                rule_codes=[JUDICIAL_RULE_CODE],
                finding_keys=[],
                chunk_ids=[UUID(invalid_chunk)],
                reason_code="none",
            ), execution

        monkeypatch.setattr(service, "select_answer", bad_selector)
        failed = await ask(client, case, "只看司法证据")
        assert failed.status_code == 502, failed.text
        assert chunk_id not in failed.text

    async def timeout(*args: Any) -> Any:
        raise model.AssistantModelError("timeout")

    monkeypatch.setattr(service, "select_answer", timeout)
    assert (await ask(client, case, "sensitive question")).status_code == 504
    assert await persisted_state() == before


async def test_completed_response_uses_report_replacement_and_hides_original(
    client: httpx.AsyncClient, cases: dict[str, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = cases["003"]
    reviewer_id, reviewer_headers = await identity("reviewer")
    async with app.state.session_factory.begin() as session:
        finding = await session.scalar(
            select(AIFinding).where(AIFinding.run_id == case.run_id)
        )
        assert finding is not None
        rejected = AIFinding(
            run_id=case.run_id,
            finding_key="rejected-original",
            version=1,
            risk_type=finding.risk_type,
            claim_code=finding.claim_code,
            title="被驳回原稿",
            summary=finding.summary,
            reasoning=finding.reasoning,
            citations=finding.citations,
            evidence_sufficiency="insufficient",
            confidence_level="high",
            review_recommended=True,
        )
        session.add(rejected)
        await session.flush()
    modified = await client.post(
        f"/reviews/{finding.id}",
        headers={**reviewer_headers, "Idempotency-Key": "change"},
        json={
            "decision": "modify",
            "finding_version": 1,
            "reason": "已核对案卷",
            "replacement": {
                "finding_key": finding.finding_key,
                "risk_type": finding.risk_type,
                "claim_code": finding.claim_code,
                "title": "人工替换稿",
                "summary": "人工复核后的摘要",
                "reasoning": finding.reasoning,
                "evidence_citations": finding.citations,
                "evidence_sufficiency": "sufficient",
                "confidence_level": "high",
                "review_recommended": False,
            },
        },
    )
    assert modified.status_code == 200, modified.text
    removed = await client.post(
        f"/reviews/{rejected.id}",
        headers={**reviewer_headers, "Idempotency-Key": "reject"},
        json={"decision": "reject", "finding_version": 1, "reason": "已复核并驳回"},
    )
    assert removed.status_code == 200, removed.text
    sessions = app.state.session_factory
    async with sessions.begin() as session:
        run = await session.get(WorkflowRun, case.run_id)
        human = await session.scalar(
            select(HumanFindingVersion).where(
                HumanFindingVersion.reviewer_id == reviewer_id
            )
        )
        assert run is not None and human is not None
        assert run.status == "resume_queued"
        run.status = "running"
    report_id = await generate_report(sessions, case.run_id)
    before = await persisted_state()

    completed = await ask(client, case, "请综合说明最终报告发现")
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["report_id"] == str(report_id) and body["run_status"] == "completed"
    (final,) = kind(body, "findings")["findings"]
    assert final["id"] == str(human.id) and final["version"] == human.version
    assert final["source"] == "human" and final["title"] == "人工替换稿"
    assert final["summary"] == "人工复核后的摘要"
    assert "被驳回原稿" not in completed.text and finding.title not in completed.text

    original = await ask(client, case, "请显示原始AI发现，包括被驳回的原稿")
    assert original.status_code == 200, original.text
    assert {
        item["id"] for item in kind(original.json(), "original_ai")["findings"]
    } == {str(finding.id), str(rejected.id)}
    comparison = await ask(client, case, "请对比最终结论与原始AI发现")
    assert comparison.status_code == 200, comparison.text
    assert kind(comparison.json(), "original_ai")["findings"]

    async def check_final_context(
        settings: Settings, question: str, context: dict[str, Any]
    ) -> Any:
        assert not context["original_ai_allowed"]
        assert all(item["source"] != "original_ai" for item in context["findings"])
        return await model.select_answer(settings, question, context)

    monkeypatch.setattr(service, "select_answer", check_final_context)
    for question in (
        "请说明是否审核过原始AI发现，只看最终结论。",
        "请说明是否审核过原始AI发现。",
        "请说明报告是否显示原始AI发现。",
        "请查看原始AI发现是否经过人工复核。",
        "请显示原始AI发现，只看最终结论。",
    ):
        response = await ask(client, case, question)
        assert response.status_code == 200, response.text
        assert kind(response.json(), "findings")["findings"] == [final]
        assert "被驳回原稿" not in response.text

    async def smuggle_original(*args: Any) -> Any:
        _, execution = await model.select_answer(*args)
        return AssistantSelection(
            status="answer",
            sections=["original_ai"],
            rule_codes=[],
            finding_keys=[f"original_ai:{rejected.id}:1"],
            chunk_ids=[],
            reason_code="none",
        ), execution

    monkeypatch.setattr(service, "select_answer", smuggle_original)
    assert (
        await ask(client, case, "请说明最终结论，不要显示原始AI")
    ).status_code == 502
    assert await persisted_state() == before

    async def change_saved_report(*args: Any) -> Any:
        async with sessions.begin() as session:
            report = await session.get(RiskReport, report_id)
            assert report is not None
            older = dict(report.snapshot)
            older.pop("as_of_date")
            older.pop("model_execution")
            older["retrieved_evidence"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"source_uri", "title"}
                }
                for item in older["retrieved_evidence"]
            ]
            report.snapshot = older
        return await model.select_answer(*args)

    monkeypatch.setattr(service, "select_answer", change_saved_report)
    assert (await ask(client, case, "综合")).status_code == 409
    monkeypatch.setattr(service, "select_answer", model.select_answer)
    async with sessions.begin() as session:
        evidence = await session.get(
            Evidence, UUID(body["citations"][0]["evidence_id"])
        )
        assert evidence is not None
        evidence.title = "报告保存后改过的标题"
        evidence.source_uri = "https://example.test/live-source"
    legacy = await ask(client, case, "综合")
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["investigation_model_execution"]["mode"] == "unknown"
    assert legacy.json()["citations"] == [
        item | {"title": "证据标题未记录", "source_uri": "未记录"}
        for item in body["citations"]
    ]
    assert "报告保存后改过的标题" not in legacy.text
    assert "https://example.test/live-source" not in legacy.text
    assert kind(legacy.json(), "findings")["findings"] == [final]

    async def start_new_run(*args: Any) -> Any:
        _, analyst = await identity("analyst")
        rerun = await client.post(
            f"/investigations/{case.investigation_id}/runs",
            headers={**analyst, "Idempotency-Key": "assistant-in-flight"},
        )
        assert rerun.status_code == 200, rerun.text
        assert rerun.json()["id"] != str(case.run_id)
        return await model.select_answer(*args)

    monkeypatch.setattr(service, "select_answer", start_new_run)
    assert (await ask(client, case, "综合")).status_code == 409


async def test_review_change_while_model_waits_discards_stale_answer(
    client: httpx.AsyncClient, cases: dict[str, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = cases["003"]
    reviewer_id, _ = await identity("reviewer")

    async def change_review(*args: Any) -> Any:
        engine = app.state.session_factory.kw["bind"]
        assert engine.pool.checkedout() == 0
        async with app.state.session_factory.begin() as session:
            run = await session.get(WorkflowRun, case.run_id, with_for_update=True)
            finding = await session.scalar(
                select(AIFinding).where(AIFinding.run_id == case.run_id)
            )
            assert run is not None and finding is not None
            session.add(
                ReviewDecision(
                    run_id=run.id,
                    ai_finding_id=finding.id,
                    finding_version=1,
                    reviewer_id=reviewer_id,
                    idempotency_key="in-flight-review",
                    decision="reject",
                    reason="Persisted during model call",
                )
            )
            # Partial review leaves the run awaiting review.
        return await model.select_answer(*args)

    monkeypatch.setattr(service, "select_answer", change_review)
    response = await ask(client, case, "综合")
    assert response.status_code == 409, response.text
