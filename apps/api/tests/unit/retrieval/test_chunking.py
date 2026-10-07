from datetime import date
from hashlib import sha256
from uuid import uuid4

from tracerisk.evidence.models import Evidence
from tracerisk.retrieval.chunking import chunk_evidence


def test_stable_paragraphs_and_distinct_hashes() -> None:
    source = Evidence(
        id=uuid4(),
        company_id=uuid4(),
        source_type="news",
        risk_type="news",
        observed_at=date(2026, 9, 16),
        fact_type="news_sentiment",
        fact_key="a",
        fact_value="negative",
        polarity="negative",
        content=" 第一段 \n\n \n第二段\n\n",
        content_hash="a" * 64,
    )
    chunks = chunk_evidence(source)
    assert chunks == chunk_evidence(source)
    assert [c.content for c in chunks] == ["第一段", "第二段"]
    assert len({c.id for c in chunks}) == 2
    assert all(c.evidence_content_hash == source.content_hash for c in chunks)
    assert chunks[0].content_hash == sha256("第一段".encode()).hexdigest()
    source.source_type = "judicial"
    assert len(chunk_evidence(source)) == 1
    source.content = " \n\n "
    assert chunk_evidence(source) == ()
