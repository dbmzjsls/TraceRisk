from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.models import AIFinding
from tracerisk.audit.service import append_audit_event
from tracerisk.companies.models import Company
from tracerisk.evidence.conflicts import load_retrieved_conflicts
from tracerisk.evidence.coverage import SOURCE_ORDER, load_coverage
from tracerisk.evidence.service import load_frozen_retrieval
from tracerisk.investigations.models import Investigation, WorkflowRun
from tracerisk.reports.models import RiskReport
from tracerisk.reports.render import render_report
from tracerisk.reviews.models import HumanFindingVersion, ReviewDecision
from tracerisk.rules.models import RuleFinding, RuleFindingEvidence
from tracerisk.rules.references import resolve_rule_evidence_targets
from tracerisk.workflows.events import append_workflow_event
from tracerisk.workflows.routing import review_reasons


async def generate_report(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
    review_id: UUID | None = None,
) -> UUID:
    async with sessions.begin() as session:
        investigation = await session.scalar(
            select(Investigation)
            .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
            .where(WorkflowRun.id == run_id)
            .with_for_update(of=Investigation)
        )
        run = await session.get(WorkflowRun, run_id, with_for_update=True)
        if (
            investigation is None
            or run is None
            or investigation.current_run_id != run_id
        ):
            raise ValueError("Invalid report context")
        if review_id is not None:
            selected_review = await session.get(ReviewDecision, review_id)
            if selected_review is None or selected_review.run_id != run_id:
                raise ValueError("Invalid report review")
        existing = await session.scalar(
            select(RiskReport).where(RiskReport.run_id == run_id)
        )
        if existing is not None and run.status == "completed":
            return existing.id
        if run.status != "running":
            raise ValueError("Run is not generating a report")
        company = await session.get(Company, investigation.company_id)
        rules = (
            await session.scalars(
                select(RuleFinding)
                .where(RuleFinding.run_id == run_id)
                .order_by(RuleFinding.rule_code)
            )
        ).all()
        findings = (
            await session.scalars(
                select(AIFinding)
                .where(AIFinding.run_id == run_id)
                .order_by(AIFinding.finding_key)
            )
        ).all()
        decisions = (
            await session.scalars(
                select(ReviewDecision)
                .where(ReviewDecision.run_id == run_id)
                .order_by(ReviewDecision.id)
            )
        ).all()
        if review_id is not None and not any(d.id == review_id for d in decisions):
            raise ValueError("Invalid report review")
        human_versions = {
            h.review_decision_id: h
            for h in (
                await session.scalars(
                    select(HumanFindingVersion).where(
                        HumanFindingVersion.run_id == run_id
                    )
                )
            ).all()
        }
        frozen_rows = await load_frozen_retrieval(session, run_id=run_id)
        chunk_rows = [(chunk, row.risk_type) for chunk, row in frozen_rows]
        if any(chunk.company_id != investigation.company_id for chunk, _ in chunk_rows):
            raise ValueError("Retrieved evidence belongs to another company")
        coverage = await load_coverage(
            session,
            company_id=investigation.company_id,
            as_of_date=investigation.as_of_date,
        )
        conflict_evaluation = await load_retrieved_conflicts(
            session,
            run_id=run_id,
            as_of_date=investigation.as_of_date,
        )
        score = sum(rule.score for rule in rules)
        if not 0 <= score <= 95:
            raise ValueError("Invalid rule score in persisted report context")
        reasons = review_reasons(
            rule_score=score,
            evidence_sufficient=(
                coverage.is_sufficient and not conflict_evaluation.has_unresolved
            ),
            conflict=conflict_evaluation.requires_review,
            model_evidence_insufficient=any(
                f.evidence_sufficiency == "insufficient" for f in findings
            ),
            model_recommended=any(f.review_recommended for f in findings),
            support_reasons=(f.support_reason for f in findings),
        )
        by_finding = {f.id: f for f in findings}
        reviewed: dict[UUID, ReviewDecision] = {}
        run_review = None
        for decision in decisions:
            if decision.ai_finding_id is None:
                if findings:
                    raise ValueError("Run-level review cannot decide AI findings")
                run_review = decision
            else:
                finding = by_finding.get(decision.ai_finding_id)
                if finding is None or decision.finding_version != finding.version:
                    raise ValueError("Stale or invalid finding review")
                human = human_versions.get(decision.id)
                if (decision.decision == "modify") != (human is not None):
                    raise ValueError("Modified review requires its human version")
                reviewed[finding.id] = decision
        if reasons and (
            (findings and len(reviewed) != len(findings))
            or (not findings and run_review is None)
        ):
            raise ValueError("Report requires complete human review")
        references = []
        rule_references: list[RuleFindingEvidence] = []
        references_by_rule: dict[UUID, list[RuleFindingEvidence]] = {}
        for rule in rules:
            refs = list(
                (
                    await session.scalars(
                        select(RuleFindingEvidence)
                        .where(RuleFindingEvidence.rule_finding_id == rule.id)
                        .order_by(RuleFindingEvidence.evidence_id)
                    )
                ).all()
            )
            references_by_rule[rule.id] = refs
            rule_references.extend(refs)
        targets = resolve_rule_evidence_targets(rule_references, frozen_rows)
        chunks_by_id = {chunk.id: chunk for chunk, _ in frozen_rows}
        for rule in rules:
            refs = references_by_rule[rule.id]
            for ref in refs:
                target = targets[(ref.rule_finding_id, ref.evidence_id)]
                chunk = chunks_by_id.get(target.chunk_id) if target.chunk_id else None
                references.append(
                    {
                        "rule_finding_id": str(rule.id),
                        "evidence_id": str(ref.evidence_id),
                        "content_hash": ref.content_hash,
                        "chunk_id": str(chunk.id) if chunk else None,
                        "chunk_content_hash": chunk.content_hash if chunk else None,
                        "title": chunk.fact_key if chunk else None,
                        "content": chunk.content if chunk else None,
                    }
                )
        original = [
            {
                "id": str(f.id),
                "finding_key": f.finding_key,
                "version": f.version,
                "risk_type": f.risk_type,
                "claim_code": f.claim_code,
                "title": f.title,
                "summary": f.summary,
                "reasoning": f.reasoning,
                "citations": f.citations,
                "evidence_sufficiency": f.evidence_sufficiency,
                "confidence_level": f.confidence_level,
                "review_recommended": f.review_recommended,
                "support_status": f.support_status,
                "support_reason": f.support_reason,
            }
            for f in findings
        ]
        final = []
        for item, finding in zip(original, findings, strict=True):
            selected_decision = reviewed.get(finding.id)
            if selected_decision is not None and selected_decision.decision == "reject":
                continue
            human = (
                human_versions.get(selected_decision.id)
                if selected_decision is not None
                else None
            )
            final.append(
                {
                    **(human.replacement if human else item),
                    "id": str(human.id if human else finding.id),
                    "version": human.version if human else finding.version,
                    "source": "human" if human else "ai",
                    "original_ai_finding_id": str(finding.id),
                    "human_version": human.version if human else None,
                }
            )
        snapshot: dict[str, object] = {
            "snapshot_version": "stage6-v1",
            "notice": "合成数据，仅供演示",
            "run_id": str(run_id),
            "company": {
                "id": str(investigation.company_id),
                "name": company.name if company else "未知企业",
            },
            "rule_score": score,
            "rules": [
                {
                    "id": str(r.id),
                    "rule_code": r.rule_code,
                    "rule_version": r.rule_version,
                    "score": r.score,
                    "parameters": r.parameters,
                }
                for r in rules
            ],
            "ai_findings": original,
            "human_decisions": [
                {
                    "id": str(d.id),
                    "ai_finding_id": str(d.ai_finding_id) if d.ai_finding_id else None,
                    "finding_version": d.finding_version,
                    "reviewer_id": str(d.reviewer_id),
                    "decision": d.decision,
                    "reason": d.reason,
                    "human_finding_version_id": (
                        str(human_versions[d.id].id) if d.id in human_versions else None
                    ),
                    "human_version": (
                        human_versions[d.id].version if d.id in human_versions else None
                    ),
                    "replacement": (
                        human_versions[d.id].replacement
                        if d.id in human_versions
                        else None
                    ),
                }
                for d in decisions
            ],
            "final_findings": final,
            "evidence_references": references,
            "retrieved_evidence": [
                {
                    "risk_type": retrieval.risk_type,
                    "chunk_id": str(c.id),
                    "evidence_id": str(c.evidence_id),
                    "observed_at": c.observed_at.isoformat(),
                    "fact_type": c.fact_type,
                    "fact_key": c.fact_key,
                    "fact_value": c.fact_value,
                    "polarity": c.polarity,
                    "content_hash": c.content_hash,
                    "evidence_content_hash": c.evidence_content_hash,
                    "source_type": c.source_type,
                    "rank": retrieval.rank,
                    "content": c.content,
                }
                for c, retrieval in frozen_rows
            ],
            "coverage": {
                "scope": list(SOURCE_ORDER),
                "entries": [
                    {
                        "source_type": entry.source_type,
                        "status": entry.status,
                        "coverage_id": (
                            str(entry.coverage_id) if entry.coverage_id else None
                        ),
                        "checked_at": (
                            entry.checked_at.isoformat() if entry.checked_at else None
                        ),
                    }
                    for entry in coverage.entries
                ],
                "insufficiency_details": list(coverage.insufficiency_details),
                "evidence_sufficiency": (
                    "sufficient"
                    if coverage.is_sufficient and not conflict_evaluation.has_unresolved
                    else "insufficient"
                ),
            },
            "conflicts": [
                {
                    "company_id": str(conflict.company_id),
                    "risk_type": conflict.risk_type,
                    "fact_type": conflict.fact_type,
                    "fact_key": conflict.fact_key,
                    "authority_source": conflict.authority_source,
                    "resolved_value": conflict.resolved_value,
                    "resolution_state": (
                        "resolved"
                        if conflict.resolved_value is not None
                        else "unresolved"
                    ),
                    "sides": [
                        {
                            "source_type": side.source_type,
                            "fact_value": side.fact_value,
                            "observed_at": side.observed_at.isoformat(),
                            "evidence_id": str(side.evidence_id),
                            "content_hash": side.content_hash,
                        }
                        for side in conflict.sides
                    ],
                }
                for conflict in conflict_evaluation.conflicts
            ],
            "review_reasons": reasons,
            "evidence_sufficiency": (
                "sufficient"
                if coverage.is_sufficient and not conflict_evaluation.has_unresolved
                else "insufficient"
            ),
        }
        if run_review is not None:
            snapshot.update(
                review_id=str(run_review.id),
                decision=run_review.decision,
                reason=run_review.reason,
                kind="workflow_spike",
            )
        report = RiskReport(
            run_id=run_id,
            snapshot=snapshot,
            html_content=render_report(snapshot),
        )
        session.add(report)
        await session.flush()
        changed = await session.scalar(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.status == "running")
            .values(status="completed")
            .returning(WorkflowRun.id)
        )
        if changed is None:
            raise ValueError("Run state changed before report commit")
        await append_audit_event(
            session,
            event_type="report.generated",
            action_key=f"report:{report.id}:generated",
            actor_id=None,
            investigation_id=investigation.id,
            run_id=run_id,
            resource_type="risk_report",
            resource_id=report.id,
            metadata={"rule_score": score, "run_status": "completed"},
        )
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="node:generate_report:completed",
            event_type="node.completed",
            node_name="generate_report",
            run_status="completed",
        )
        await append_workflow_event(
            session,
            run_id=run_id,
            event_key="run:completed",
            event_type="run.completed",
            run_status="completed",
        )
        return report.id
