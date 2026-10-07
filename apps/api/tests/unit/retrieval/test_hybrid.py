from datetime import date
from uuid import UUID

import pytest

from tracerisk.retrieval.chunking import ChunkDraft
from tracerisk.retrieval.hybrid import RetrievalCandidate, fuse_rankings
from tracerisk.retrieval.keyword import RankedScore


def candidate(number, evidence=None, risk="news", digest="a" * 64):
    return RetrievalCandidate(
        chunk=ChunkDraft(
            id=UUID(int=number),
            evidence_id=UUID(int=evidence or number),
            company_id=UUID(int=100),
            source_type=risk,
            risk_type=risk,
            observed_at=date(2026, 9, 16),
            fact_type="news_sentiment",
            fact_key="news",
            fact_value="negative",
            polarity="negative",
            content="negative news",
            content_hash="b" * 64,
            evidence_content_hash=digest,
        ),
        title="News",
    )


def ranking(*ids):
    return tuple(
        RankedScore(UUID(int=n), rank, 1000 / rank) for rank, n in enumerate(ids, 1)
    )


def test_rrf_uses_ranks_and_keyword_then_vector_then_chunk_ties():
    candidates = [candidate(1), candidate(2)]
    rows = fuse_rankings(candidates, ranking(2, 1), ranking(1, 2))
    assert [r.chunk.id.int for r in rows] == [2, 1]
    assert rows[0].score == rows[1].score == pytest.approx(1 / 61 + 1 / 62)
    assert (rows[0].keyword_rank, rows[0].vector_rank) == (1, 2)
    tied_keyword = tuple(RankedScore(UUID(int=n), 1, n * 99.0) for n in (1, 2))
    assert [
        r.chunk.id.int for r in fuse_rankings(candidates, tied_keyword, ranking(2, 1))
    ] == [2, 1]
    tied_vector = tuple(RankedScore(UUID(int=n), 1, 999.0 - n) for n in (2, 1))
    assert [
        r.chunk.id.int
        for r in fuse_rankings(candidates[::-1], tied_keyword, tied_vector)
    ] == [1, 2]


def test_best_chunk_per_evidence_version_then_top_five():
    candidates = [candidate(1), candidate(2, evidence=1)] + [
        candidate(n) for n in range(3, 9)
    ]
    ranks = ranking(2, 1, 3, 4, 5, 6, 7, 8)
    rows = fuse_rankings(candidates, ranks, ranks)
    assert [r.chunk.id.int for r in rows] == [2, 3, 4, 5, 6]
    assert [r.rank for r in rows] == [1, 2, 3, 4, 5]
    assert rows == fuse_rankings(candidates[::-1], ranks, ranks)
    distinct_versions = [candidate(1), candidate(2, evidence=1, digest="c" * 64)]
    assert len(fuse_rankings(distinct_versions, ranking(1, 2), ranking(2, 1))) == 2


def test_single_ranker_and_empty_candidates():
    rows = fuse_rankings([candidate(1)], (), ranking(1))
    assert rows[0].keyword_rank is None
    assert rows[0].score == pytest.approx(1 / 61)
    assert fuse_rankings([], (), ()) == ()


def test_fusion_rejects_cross_risk_and_unscoped_rankings():
    with pytest.raises(ValueError):
        fuse_rankings([candidate(1), candidate(2, risk="business")], ranking(1, 2), ())
    with pytest.raises(ValueError):
        fuse_rankings([candidate(1)], ranking(2), ())
