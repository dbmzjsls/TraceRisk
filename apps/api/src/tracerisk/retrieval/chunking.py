"""Pure, deterministic v1 chunk construction; never rewrite historical chunks."""

import re
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from tracerisk.evidence.models import Evidence

CHUNK_SCHEMA_VERSION = "v1"


@dataclass(frozen=True)
class ChunkDraft:
    id: UUID
    evidence_id: UUID
    company_id: UUID
    source_type: str
    risk_type: str
    observed_at: date
    fact_type: str
    fact_key: str
    fact_value: str
    polarity: str
    content: str
    content_hash: str
    evidence_content_hash: str
    chunk_schema_version: str = CHUNK_SCHEMA_VERSION


def chunk_evidence(evidence: Evidence) -> tuple[ChunkDraft, ...]:
    paragraphs = (
        re.split(r"\r?\n[ \t\r]*\n", evidence.content)
        if evidence.source_type == "news"
        else [evidence.content]
    )
    texts = [paragraph.strip() for paragraph in paragraphs if paragraph.strip()]
    return tuple(
        ChunkDraft(
            id=uuid5(
                NAMESPACE_URL,
                f"tracerisk:chunk:{evidence.id}:{CHUNK_SCHEMA_VERSION}:{ordinal}",
            ),
            evidence_id=evidence.id,
            company_id=evidence.company_id,
            source_type=evidence.source_type,
            risk_type=evidence.risk_type,
            observed_at=evidence.observed_at,
            fact_type=evidence.fact_type,
            fact_key=evidence.fact_key,
            fact_value=evidence.fact_value,
            polarity=evidence.polarity,
            content=content,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            evidence_content_hash=evidence.content_hash,
        )
        for ordinal, content in enumerate(texts)
    )
