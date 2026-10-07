import asyncio
import random
import sys
from types import SimpleNamespace

import pytest

from tracerisk.retrieval.embeddings import (
    BGE_QUERY_INSTRUCTION,
    BGEEmbedding,
    FakeEmbedding,
    validate_vectors,
)


def test_fake_is_normalized_deterministic_and_separate() -> None:
    provider = FakeEmbedding()
    before = random.getstate()
    documents = asyncio.run(provider.embed_documents([" hello  world ", "other"]))
    queries = asyncio.run(provider.embed_queries(["hello world"]))
    assert documents[0] == queries[0]
    assert documents[0] != documents[1]
    assert sum(x * x for x in documents[0]) == pytest.approx(1)
    assert random.getstate() == before
    assert "sentence_transformers" not in sys.modules


@pytest.mark.parametrize(
    "vectors,count",
    [
        ([], 1),
        ([[1.0] * 511], 1),
        ([[float("nan")] * 512], 1),
        ([[float("inf")] * 512], 1),
        ([[0.0] * 512], 1),
    ],
)
def test_reject_invalid_vectors(vectors: list[list[float]], count: int) -> None:
    with pytest.raises(ValueError):
        validate_vectors(vectors, count)


def test_blank_rejected() -> None:
    with pytest.raises(ValueError):
        asyncio.run(FakeEmbedding().embed_documents([" "]))


def test_bge_lazy_cpu_query_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class Model:
        def __init__(self, name: str, *, device: str) -> None:
            assert name == "BAAI/bge-small-zh-v1.5"
            assert device == "cpu"

        def encode(self, texts: list[str], *, normalize_embeddings: bool) -> object:
            assert normalize_embeddings
            calls.append(texts)
            return SimpleNamespace(tolist=lambda: [[1.0] * 512 for _ in texts])

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model)
    )
    provider = BGEEmbedding()
    assert calls == []
    asyncio.run(provider.embed_documents(["正文"]))
    asyncio.run(provider.embed_queries(["风险"]))
    assert calls == [["正文"], [BGE_QUERY_INSTRUCTION + "风险"]]
