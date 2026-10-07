"""Embedding contracts. Importing this module never imports or downloads BGE."""

import asyncio
import importlib
import math
import unicodedata
from collections.abc import Sequence
from hashlib import sha256
from typing import Protocol

DIMENSIONS = 512
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimensions: int
    distance: str

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...


def normalized_texts(texts: Sequence[str]) -> list[str]:
    normalized = [
        " ".join(unicodedata.normalize("NFKC", text).split()) for text in texts
    ]
    if any(not text for text in normalized):
        raise ValueError("Embedding text must not be blank")
    return normalized


def validate_vectors(
    vectors: Sequence[Sequence[float]], count: int
) -> list[list[float]]:
    if len(vectors) != count:
        raise ValueError("Embedding batch cardinality mismatch")
    result = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != DIMENSIONS:
            raise ValueError("Embedding requires 512 dimensions")
        if not all(math.isfinite(value) for value in values) or not any(values):
            raise ValueError("Embedding must be finite and nonzero")
        result.append(values)
    return result


class FakeEmbedding:
    provider = "fake"
    model = "fake-sha256-v1"
    dimensions = DIMENSIONS
    distance = "cosine"

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in normalized_texts(texts):
            values = [
                int.from_bytes(sha256(f"{text}:{i}".encode()).digest()[:4], "big")
                / 2**31
                - 1
                for i in range(DIMENSIONS)
            ]
            norm = math.sqrt(sum(value * value for value in values))
            vectors.append([value / norm for value in values])
        return validate_vectors(vectors, len(texts))

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.embed_documents(texts)


class BGEEmbedding:
    provider = "bge"
    model = "BAAI/bge-small-zh-v1.5"
    dimensions = DIMENSIONS
    distance = "cosine"

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        def encode() -> list[list[float]]:
            # Optional dependency and model loading happen only on explicit use.
            sentence_transformers = importlib.import_module("sentence_transformers")

            if not hasattr(self, "_model"):
                self._model = sentence_transformers.SentenceTransformer(
                    self.model, device="cpu"
                )
            return validate_vectors(
                self._model.encode(texts, normalize_embeddings=True).tolist(),
                len(texts),
            )

        return await asyncio.to_thread(encode)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(normalized_texts(texts))

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(
            [BGE_QUERY_INSTRUCTION + text for text in normalized_texts(texts)]
        )
