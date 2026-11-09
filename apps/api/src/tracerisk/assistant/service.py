import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.schemas import EvidenceCitation, RiskType
from tracerisk.assistant.model import PROMPT_VERSION, AssistantModelError, select_answer
from tracerisk.assistant.schemas import (
    AnswerRunStatus,
    AssistantAnswerRequest,
    AssistantAnswerResponse,
    AssistantFinding,
    AssistantSection,
    AssistantSelection,
    FindingSource,
    SectionKind,
    SupportReason,
    SupportStatus,
)
from tracerisk.config import Settings
from tracerisk.evidence.service import RetrievalIntegrityError
from tracerisk.investigations.models import Investigation, WorkflowRun
from tracerisk.investigations.read_model import load_investigation_detail
from tracerisk.investigations.schemas import (
    CoverageResponse,
    ModelExecutionResponse,
    RetrievedEvidenceResponse,
    RuleEvidenceResponse,
    RuleFindingResponse,
)
from tracerisk.reports.models import RiskReport
from tracerisk.rules.business import BUSINESS_RULE_CODE
from tracerisk.rules.intellectual_property import CORE_IP_RULE_CODE
from tracerisk.rules.judicial import JUDICIAL_RULE_CODE
from tracerisk.rules.news import NEWS_RULE_CODE

logger = logging.getLogger("uvicorn.error").getChild("assistant")

RULES: dict[str, tuple[RiskType, str]] = {
    BUSINESS_RULE_CODE: ("business", "基准日仍有效的经营异常"),
    JUDICIAL_RULE_CODE: ("judicial", "基准日存在未结案件"),
    NEWS_RULE_CODE: ("news", "基准日前含当日365天内至少两条独立负面报道"),
    CORE_IP_RULE_CODE: ("intellectual_property", "核心专利失效或存在权属争议"),
}
SOURCES = {
    "business": "工商",
    "judicial": "司法",
    "news": "舆情",
    "intellectual_property": "知识产权",
}
COVERAGE_STATUSES = {
    "available": "可用",
    "unavailable": "不可用",
    "stale": "已过期",
    "missing": "缺失",
}
REVIEW_REASONS = {
    "high_risk": "规则分达到高风险阈值（60分）",
    "evidence_insufficient": "来源覆盖不足或冲突尚未解决",
    "conflict": "同一事实存在跨来源冲突",
    "model_evidence_insufficient": "模型发现的证据不足",
    "model_recommended": "模型或支持性校验建议复核",
    "unverified_free_text_extension": "自然语言扩展尚无法确认支持程度",
}
REVIEW_ACTIONS = {
    "business": "核对经营异常的列入、移出记录及基准日状态。",
    "judicial": "核对案号、案件状态及基准日后的进展。",
    "news": "核对报道的独立来源、发布日期及后续更正。",
    "intellectual_property": "核对核心专利的法律状态、权属登记及争议进展。",
}
SECTION_TITLES: dict[SectionKind, str] = {
    "overview": "调查概览",
    "rules": "规则命中",
    "findings": "调查发现",
    "original_ai": "原始AI发现",
    "coverage": "来源覆盖",
    "evidence": "引用证据",
    "review_actions": "人工核查事项",
    "limits": "判断边界",
}
PENDING = "待人工复核的调查结果"
COMPLETED = "已完成，以保存的报告为准"
ORIGINAL_AI = r"(?:原始\s*AI|原\s*AI|AI\s*原始|复核前的?\s*AI|original\s+AI)"


class AssistantContextChanged(ValueError):
    """The requested current Run is missing, unready or has changed."""


class AssistantInvestigationNotFound(ValueError):
    """The investigation does not exist."""


class ReportFinding(BaseModel):
    id: UUID
    version: int = Field(gt=0)
    risk_type: RiskType
    title: str
    summary: str
    support_status: SupportStatus
    support_reason: SupportReason
    citations: list[dict[str, object]]


class FinalReportFinding(ReportFinding):
    source: Literal["ai", "human"] | None = None


class ReportRuleReference(RuleEvidenceResponse):
    rule_finding_id: UUID


class SavedReportRule(RuleFindingResponse):
    evidence: list[RuleEvidenceResponse] = Field(default_factory=list)


class SavedReportCompany(BaseModel):
    id: UUID
    name: str


class SavedReportContext(BaseModel):
    snapshot_version: Literal["stage5-v1", "stage6-v1"]
    run_id: UUID
    as_of_date: date | None = None
    company: SavedReportCompany
    rule_score: Annotated[int, Field(ge=0, le=95)]
    rules: list[SavedReportRule]
    evidence_references: list[ReportRuleReference]
    retrieved_evidence: list[dict[str, object]]
    ai_findings: list[ReportFinding]
    final_findings: list[FinalReportFinding]
    coverage: CoverageResponse
    review_reasons: list[str]
    model_execution: ModelExecutionResponse | None = None


@dataclass(frozen=True)
class AnswerContext:
    run_id: UUID
    company_name: str
    as_of_date: date
    run_status: AnswerRunStatus
    report_id: UUID | None
    rule_score: int
    review_reasons: list[str]
    model_execution: ModelExecutionResponse
    coverage: CoverageResponse
    rules: dict[str, RuleFindingResponse]
    findings: dict[str, AssistantFinding]
    evidence: dict[UUID, RetrievedEvidenceResponse]
    digest: str


def _report_evidence(
    saved: SavedReportContext, frozen: dict[UUID, RetrievedEvidenceResponse]
) -> dict[UUID, RetrievedEvidenceResponse]:
    result = {}
    for raw in saved.retrieved_evidence:
        chunk_id = UUID(str(raw["chunk_id"]))
        chunk = frozen[chunk_id]
        # Older reports omit source labels. Resolve them only against this chunk.
        item = RetrievedEvidenceResponse.model_validate(
            {
                "title": chunk.title,
                "source_uri": chunk.source_uri,
                **raw,
            }
        )
        fields = {"title", "source_uri"}
        if item.model_dump(exclude=fields) != chunk.model_dump(exclude=fields):
            raise AssistantContextChanged
        result[chunk_id] = item
    if len(result) != len(saved.retrieved_evidence) or result.keys() != frozen.keys():
        raise AssistantContextChanged
    return result


def _finding_chunks(
    finding: ReportFinding, evidence: dict[UUID, RetrievedEvidenceResponse]
) -> list[UUID]:
    resolved: list[UUID] = []
    for raw in finding.citations:
        try:
            citation = EvidenceCitation.model_validate(raw)
        except ValidationError:
            continue
        chunk = evidence.get(citation.chunk_id)
        if (
            chunk is not None
            and chunk.risk_type == finding.risk_type
            and chunk.evidence_id == citation.evidence_id
            and chunk.content_hash == citation.content_hash
            and citation.quoted_excerpt in chunk.content
            and chunk.chunk_id not in resolved
        ):
            resolved.append(chunk.chunk_id)
    return resolved


async def load_answer_context(
    sessions: async_sessionmaker[AsyncSession], payload: AssistantAnswerRequest
) -> AnswerContext:
    async with sessions() as session:
        # Consistent read across the detail reader's individual SQL statements.
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        investigation = await session.get(Investigation, payload.investigation_id)
        if investigation is None:
            raise AssistantInvestigationNotFound
        if investigation.current_run_id != payload.run_id:
            raise AssistantContextChanged
        run = await session.get(WorkflowRun, payload.run_id)
        if (
            run is None
            or run.investigation_id != investigation.id
            or run.status not in {"awaiting_review", "completed"}
        ):
            raise AssistantContextChanged
        try:
            detail = await load_investigation_detail(session, investigation)
        except (RetrievalIntegrityError, ValidationError):
            raise AssistantContextChanged from None
        if detail.retrieval_state != "ready":
            raise AssistantContextChanged
        report_id = detail.current_run.report_id
        report = await session.get(RiskReport, report_id) if report_id else None
        report_snapshot = report.snapshot if report else None

    status: AnswerRunStatus = (
        "completed" if run.status == "completed" else "awaiting_review"
    )
    company_name = detail.company_name
    score, reasons, execution = (
        detail.rule_score,
        detail.review_reasons,
        detail.model_execution,
    )
    rules = detail.rule_findings
    coverage = detail.coverage
    evidence = {item.chunk_id: item for item in detail.retrieved_evidence}
    findings: dict[str, AssistantFinding] = {}

    def add_finding(key: str, finding: ReportFinding, source: FindingSource) -> None:
        findings[key] = AssistantFinding(
            **finding.model_dump(exclude={"citations", "source"}),
            source=source,
            citation_chunk_ids=_finding_chunks(finding, evidence),
        )

    if status == "completed":
        if report is None or report.run_id != payload.run_id:
            raise AssistantContextChanged
        try:
            saved = SavedReportContext.model_validate(report_snapshot)
            rules = [
                rule.model_copy(
                    update={
                        "evidence": [
                            ref
                            for ref in saved.evidence_references
                            if ref.rule_finding_id == rule.id
                        ],
                    }
                )
                for rule in saved.rules
            ]
            evidence = _report_evidence(saved, evidence)
        except (ValueError, KeyError):
            raise AssistantContextChanged from None
        if (
            saved.run_id != payload.run_id
            or (saved.as_of_date is not None and saved.as_of_date != detail.as_of_date)
            or saved.company.id != detail.company_id
        ):
            raise AssistantContextChanged
        company_name = saved.company.name
        score, reasons = saved.rule_score, saved.review_reasons
        execution = saved.model_execution or ModelExecutionResponse(
            mode="unknown", models=[], statuses=[], error_categories=[]
        )
        coverage = saved.coverage
        for index, final in enumerate(saved.final_findings):
            if final.source is None and saved.snapshot_version != "stage5-v1":
                raise AssistantContextChanged
            add_finding(f"final:{report_id}:{index}", final, final.source or "ai")
        for item in saved.ai_findings:
            add_finding(f"original_ai:{item.id}:{item.version}", item, "original_ai")
    else:
        if report is not None:
            raise AssistantContextChanged
        decided = {
            (item.ai_finding_id, item.finding_version) for item in detail.decisions
        }
        for pending in detail.ai_findings:
            item = ReportFinding.model_validate(
                {
                    **pending.model_dump(),
                    "id": pending.finding_id,
                    "version": pending.finding_version,
                }
            )
            if (item.id, item.version) not in decided:
                add_finding(f"pending_ai:{item.id}:{item.version}", item, "pending_ai")
            add_finding(f"original_ai:{item.id}:{item.version}", item, "original_ai")

    fingerprint = json.dumps(
        {"detail": detail.model_dump(mode="json"), "report": report_snapshot},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return AnswerContext(
        payload.run_id,
        company_name,
        detail.as_of_date,
        status,
        report_id,
        score,
        reasons,
        execution,
        coverage,
        {rule.rule_code: rule for rule in rules},
        findings,
        evidence,
        sha256(fingerprint.encode("utf-8")).hexdigest(),
    )


def _allow_original_ai(question: str) -> bool:
    if re.search(
        rf"(?:不要|不用|无需|不必|不想|别|不是|不看|不显示|不展示|without|do not|don't)"
        rf"[^。！？,，]{{0,16}}{ORIGINAL_AI}",
        question,
        re.I,
    ):
        return False
    return bool(
        re.search(
            rf"(?:看|显示|展示|查看|列出|解释|对比|说明|提供|show|list|explain|compare)"
            rf"[^。！？,，]{{0,20}}{ORIGINAL_AI}|^\s*{ORIGINAL_AI}(?:发现|结论|原稿)?[?？。\s]*$",
            question,
            re.I,
        )
    )


def _rule_chunks(
    rule: RuleFindingResponse, evidence: dict[UUID, RetrievedEvidenceResponse]
) -> set[UUID]:
    metadata = RULES.get(rule.rule_code)
    return {
        reference.chunk_id
        for reference in rule.evidence
        if reference.chunk_id is not None
        and (chunk := evidence.get(reference.chunk_id)) is not None
        and (metadata is None or chunk.risk_type == metadata[0])
        and chunk.evidence_id == reference.evidence_id
        and chunk.content_hash == reference.chunk_content_hash
    }


def _model_context(context: AnswerContext, allow_original: bool) -> dict[str, Any]:
    # Keep a fixed input budget while preserving the complete server-side fingerprint.
    evidence = dict(list(context.evidence.items())[:40])
    findings = [
        (key, item)
        for key, item in context.findings.items()
        if (item.source == "original_ai") == allow_original
    ][:40]
    if allow_original:
        # An explicit comparison may need both views.
        findings.extend(
            [
                (key, item)
                for key, item in context.findings.items()
                if item.source != "original_ai"
            ][:40]
        )
    return {
        "company_name": context.company_name[:256],
        "as_of_date": context.as_of_date.isoformat(),
        "run_id": str(context.run_id),
        "run_status": context.run_status,
        "rule_score": context.rule_score,
        "review_reasons": context.review_reasons,
        "original_ai_allowed": allow_original,
        "coverage": context.coverage.model_dump(mode="json"),
        "rules": [
            {
                "code": code,
                "score": rule.score,
                "risk_type": RULES[code][0] if code in RULES else None,
                "citation_chunk_ids": sorted(map(str, _rule_chunks(rule, evidence))),
            }
            for code, rule in context.rules.items()
        ][:20],
        "findings": [
            {
                "key": key,
                "source": item.source,
                "risk_type": item.risk_type,
                "title": item.title[:160],
                "summary": item.summary[:320],
                "support_status": item.support_status,
                "citation_chunk_ids": [
                    str(chunk_id)
                    for chunk_id in item.citation_chunk_ids
                    if chunk_id in evidence
                ],
            }
            for key, item in findings
        ],
        "evidence": [
            {
                "chunk_id": str(item.chunk_id),
                "risk_type": item.risk_type,
                "source_uri": item.source_uri[:256],
                "title": item.title[:160],
                "observed_at": item.observed_at.isoformat(),
                "content": item.content[:600],
            }
            for item in evidence.values()
        ],
    }


def _validate_selection(
    selection: AssistantSelection, context: AnswerContext, model_context: dict[str, Any]
) -> None:
    rules = {
        item["code"]: set(item["citation_chunk_ids"]) for item in model_context["rules"]
    }
    findings = {
        item["key"]: set(item["citation_chunk_ids"])
        for item in model_context["findings"]
    }
    if (
        not set(selection.rule_codes) <= rules.keys()
        or not set(selection.finding_keys) <= findings.keys()
        or (
            "original_ai" in selection.sections
            and not model_context["original_ai_allowed"]
        )
        or (
            selection.rule_codes
            and not set(selection.sections) & {"rules", "evidence", "review_actions"}
        )
    ):
        raise AssistantModelError("invalid_selection")
    related_chunks: set[str] = set()
    for code in selection.rule_codes:
        related_chunks.update(rules[code])
    for key in selection.finding_keys:
        section = (
            "original_ai"
            if context.findings[key].source == "original_ai"
            else "findings"
        )
        if section not in selection.sections:
            raise AssistantModelError("invalid_selection")
        related_chunks.update(findings[key])
    if not {str(chunk_id) for chunk_id in selection.chunk_ids} <= related_chunks:
        raise AssistantModelError("invalid_selection")


def _render(
    context: AnswerContext, selection: AssistantSelection
) -> list[AssistantSection]:
    result: list[AssistantSection] = []
    citations = selection.chunk_ids
    rule_chunks: set[UUID] = set()
    rule_lines: list[str] = []
    selected_risks: set[str] = set()
    for code in selection.rule_codes:
        rule = context.rules[code]
        if metadata := RULES.get(code):
            risk, condition = metadata
            selected_risks.add(risk)
            label = f"{SOURCES[risk]}规则（{condition}）"
        else:
            label = code
        rule_lines.append(f"{label}命中，计{rule.score}分。")
        rule_chunks.update(_rule_chunks(rule, context.evidence))

    def section(
        kind: SectionKind,
        lines: list[str],
        chunks: list[UUID] | None = None,
        findings: list[AssistantFinding] | None = None,
    ) -> AssistantSection:
        return AssistantSection(
            kind=kind,
            title=SECTION_TITLES[kind],
            lines=lines,
            citation_chunk_ids=chunks or [],
            findings=findings or [],
        )

    missing = [
        entry for entry in context.coverage.entries if entry.status != "available"
    ]
    state = PENDING if context.run_status == "awaiting_review" else COMPLETED
    limits = []
    if context.run_status == "awaiting_review":
        caution = "模型发现仍需结合证据核查。"
        limits.append(
            caution if "overview" in selection.sections else f"{PENDING}。{caution}"
        )
    if context.rule_score == 0:
        limits.append("规则分0分不代表安全。")
    if context.coverage.evidence_sufficiency != "sufficient":
        limits.append("来源覆盖不足或存在未解决的证据冲突，调查判断仍受限制。")
    if selection.status != "answer":
        limits.insert(
            0,
            {
                "scope_mismatch": "请确认要询问的企业和基准日，或重新选择调查。",
                "no_context": "请说明想了解的规则、证据或需要核查的事项。",
                "outside_scope": "调查助手只解释本次调查，无法给出贷款额度或审批意见。",
            }[selection.reason_code],
        )

    selected = [context.findings[key] for key in selection.finding_keys]
    selected_risks.update(item.risk_type for item in selected)
    for kind in selection.sections:
        if kind == "overview":
            lines = [
                f"{context.company_name}，基准日{context.as_of_date.isoformat()}。",
                f"规则合计{context.rule_score}分。{state}。",
            ]
            if context.review_reasons:
                reasons = "、".join(
                    REVIEW_REASONS.get(reason, reason)
                    for reason in context.review_reasons
                )
                lines.append(f"复核触发原因为{reasons}。")
            result.append(section(kind, lines))
        elif kind == "rules":
            result.append(
                section(
                    kind,
                    rule_lines
                    or [
                        "本次运行没有规则命中。"
                        if context.rule_score == 0
                        else "当前回答未选择具体规则。"
                    ],
                    [chunk_id for chunk_id in citations if chunk_id in rule_chunks],
                )
            )
        elif kind in {"findings", "original_ai"}:
            items = [
                item.model_copy(
                    update={
                        "citation_chunk_ids": [
                            chunk_id
                            for chunk_id in citations
                            if chunk_id in item.citation_chunk_ids
                        ]
                    }
                )
                for item in selected
                if (item.source == "original_ai") == (kind == "original_ai")
            ]
            sources = {
                "pending_ai": "待复核AI发现",
                "original_ai": "原始AI发现",
                "ai": "报告最终发现",
                "human": "报告人工版本",
            }
            support_labels = {
                "supported": "证据支持",
                "indeterminate": "支持程度待确认",
            }
            lines = [
                f"{item.title}（{sources[item.source]}，{support_labels[item.support_status]}）。"
                f"{item.summary}"
                for item in items
            ]
            if not items:
                has_findings = any(
                    (item.source == "original_ai") == (kind == "original_ai")
                    for item in context.findings.values()
                )
                lines = [
                    "当前回答未选择发现。"
                    if has_findings
                    else (
                        "本次运行未记录原始AI发现。"
                        if kind == "original_ai"
                        else "本次报告未保留最终发现。"
                        if context.run_status == "completed"
                        else "本次运行没有待审AI发现。"
                    )
                ]
            if kind == "original_ai":
                lines.insert(0, "原始AI发现仅用于回看，可能含未采纳或已驳回内容。")
            item_chunks = {
                chunk_id for item in items for chunk_id in item.citation_chunk_ids
            }
            result.append(
                section(
                    kind,
                    lines,
                    [chunk_id for chunk_id in citations if chunk_id in item_chunks],
                    items,
                )
            )
        elif kind == "coverage":
            result.append(
                section(
                    kind,
                    [
                        f"{SOURCES[entry.source_type]}来源{COVERAGE_STATUSES[entry.status]}。"
                        for entry in context.coverage.entries
                    ],
                )
            )
        elif kind == "evidence":
            result.append(
                section(
                    kind,
                    [
                        f"{context.evidence[chunk_id].title}，观察日期"
                        f"{context.evidence[chunk_id].observed_at.isoformat()}。"
                        for chunk_id in citations
                    ]
                    or ["本次回答未选出可定位的冻结证据。"],
                    citations,
                )
            )
        elif kind == "review_actions":
            lines = [
                action
                for risk, action in REVIEW_ACTIONS.items()
                if risk in selected_risks
            ]
            if "coverage" in selection.sections or not selected_risks:
                lines.extend(
                    f"补查{SOURCES[entry.source_type]}来源并记录可用性及观察日期。"
                    for entry in missing
                )
            result.append(section(kind, lines or ["核对冻结证据原件及其基准日状态。"]))
        elif kind == "limits":
            result.append(
                section(
                    kind,
                    [
                        *limits,
                        "规则分用于解释规则命中，不对应违约概率或授信审批结论。",
                    ],
                )
            )
    if limits and "limits" not in selection.sections:
        result.append(section("limits", limits))
    return result


async def answer_question(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    payload: AssistantAnswerRequest,
) -> AssistantAnswerResponse:
    context = await load_answer_context(sessions, payload)
    model_context = _model_context(context, _allow_original_ai(payload.question))
    answer_id = uuid4()
    started = time.monotonic()
    try:
        selection, execution = await select_answer(
            settings, payload.question, model_context
        )
        _validate_selection(selection, context, model_context)
    except AssistantModelError as error:
        logger.info(
            "assistant answer=%s run=%s mode=%s prompt=%s latency_ms=%s error=%s",
            answer_id,
            payload.run_id,
            settings.model_adapter,
            PROMPT_VERSION,
            int((time.monotonic() - started) * 1000),
            error.category,
        )
        raise
    sections = _render(context, selection)
    try:
        current = await load_answer_context(sessions, payload)
        if current.digest != context.digest:
            raise AssistantContextChanged
    except (AssistantContextChanged, AssistantInvestigationNotFound):
        logger.info(
            "assistant answer=%s run=%s mode=%s model=%s prompt=%s "
            "latency_ms=%s error=context_changed",
            answer_id,
            payload.run_id,
            execution.mode,
            execution.model,
            execution.prompt_version,
            execution.latency_ms,
        )
        raise AssistantContextChanged from None
    logger.info(
        "assistant answer=%s run=%s mode=%s model=%s prompt=%s "
        "latency_ms=%s usage=%s status=%s",
        answer_id,
        payload.run_id,
        execution.mode,
        execution.model,
        execution.prompt_version,
        execution.latency_ms,
        execution.usage,
        selection.status,
    )
    return AssistantAnswerResponse(
        answer_id=answer_id,
        investigation_id=payload.investigation_id,
        run_id=payload.run_id,
        report_id=context.report_id,
        question=payload.question,
        context_sha256=context.digest,
        question_sha256=sha256(payload.question.encode("utf-8")).hexdigest(),
        answered_at=datetime.now(UTC),
        status=selection.status,
        reason_code=selection.reason_code,
        sections=sections,
        citations=[context.evidence[chunk_id] for chunk_id in selection.chunk_ids],
        run_status=context.run_status,
        rule_score=context.rule_score,
        review_reasons=context.review_reasons,
        investigation_model_execution=context.model_execution,
        assistant_execution=execution,
    )
