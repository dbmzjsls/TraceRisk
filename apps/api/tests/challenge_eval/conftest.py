from hashlib import sha256
from uuid import UUID

import pytest

from tracerisk.challenge_eval.schemas import ChallengeCase, ChallengeChunk


@pytest.fixture
def valid_case() -> ChallengeCase:
    content = "An open judicial case is recorded."
    digest = sha256(content.encode("utf-8")).hexdigest()
    chunk = ChallengeChunk(
        evidence_id=UUID(int=1),
        chunk_id=UUID(int=2),
        company_id=UUID(int=3),
        source_type="judicial",
        risk_type="judicial",
        observed_at="2026-10-23",
        fact_type="judicial_case_status",
        fact_key="case-001",
        fact_value="open",
        polarity="negative",
        content=content,
        content_hash=digest,
        evidence_content_hash=digest,
    )
    return ChallengeCase(
        case_id="explicit-001",
        category="explicit_evidence",
        company_id=chunk.company_id,
        description="An explicit judicial fact.",
        chunks=[chunk],
        supporting_chunk_ids=[chunk.chunk_id],
        distractor_chunk_ids=[],
        allowed_claim_codes=["JUDICIAL_OPEN_CASE"],
        expected_action="accept",
        rationale="The chunk explicitly records an open case.",
    )
