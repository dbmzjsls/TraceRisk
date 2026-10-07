"""E2E-only combined worker: corrupt one Mock citation for all-rules-95.

Launch this file as the replacement worker command in a dedicated E2E run.
It calls the production combined supervisor after replacing only the Mock
adapter class imported by the AI generation service.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from e2e_runtime import attest_worker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tracerisk.ai import service as ai_service
from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.schemas import RiskContext, StructuredRiskFindings
from tracerisk.config import Settings
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.workflows import spike_graph
from tracerisk.workflows.service import serve

MUTATIONS = frozenset(
    {"evidence_id", "chunk_id", "content_hash", "excerpt", "outside_frozen_set"}
)
TARGET = next(item for item in generate_catalog() if item.key == "all-rules-95")
OTHER = next(item for item in generate_catalog() if item.key == "existing-judicial")


class InvalidCitationMockAdapter(MockModelAdapter):
    async def analyze_risk(self, context: RiskContext) -> StructuredRiskFindings:
        result = await super().analyze_risk(context)
        if context.company_id != TARGET.company.id or not result.findings:
            return result
        kind = os.environ["E2E_CITATION_MUTATION"]
        finding = result.findings[0]
        citation = finding.evidence_citations[0]
        if kind == "evidence_id":
            changed = citation.model_copy(update={"evidence_id": UUID(int=1)})
        elif kind == "chunk_id":
            changed = citation.model_copy(update={"chunk_id": UUID(int=2)})
        elif kind == "content_hash":
            changed = citation.model_copy(update={"content_hash": "0" * 64})
        elif kind == "excerpt":
            changed = citation.model_copy(
                update={"quoted_excerpt": citation.quoted_excerpt + " forged"}
            )
        else:
            source = OTHER.evidence[0]
            outside = uuid5(NAMESPACE_URL, f"tracerisk:chunk:{source.id}:v1:0")
            changed = citation.model_copy(
                update={
                    "evidence_id": source.id,
                    "chunk_id": outside,
                    "content_hash": source.content_hash,
                    "quoted_excerpt": source.content,
                }
            )
        replacement = finding.model_copy(
            update={"evidence_citations": [changed, *finding.evidence_citations[1:]]}
        )
        return result.model_copy(
            update={"findings": [replacement, *result.findings[1:]]}
        )


async def generate_invalid_findings(
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    settings: Settings | None = None,
) -> None:
    """Inject through the graph's imported service binding for this worker only."""
    await ai_service.generate_validate_findings(
        sessions,
        run_id=run_id,
        settings=settings,
        adapter=InvalidCitationMockAdapter(),
    )


def main() -> None:
    if os.environ.get("E2E_CITATION_MUTATION") not in MUTATIONS:
        raise ValueError("A named E2E citation mutation is required")
    proof = Path(os.environ["E2E_WORKER_PROOF"])
    attest_worker(
        Path(os.environ["E2E_OWNERSHIP_RECORD"]),
        os.environ["E2E_DB_NAME"],
        "invalid",
        proof,
    )
    ai_service.MockModelAdapter = InvalidCitationMockAdapter
    spike_graph.generate_validate_findings = generate_invalid_findings
    logging.basicConfig(level=logging.INFO)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(serve())
    finally:
        proof.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
