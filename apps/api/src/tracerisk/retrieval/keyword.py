"""Pure lexical ranking: body weight 1, title 3, structured facts 2."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

_CHINESE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]+")


@dataclass(frozen=True)
class RankedScore:
    chunk_id: UUID
    rank: int
    score: float


@dataclass(frozen=True)
class KeywordDocument:
    chunk_id: UUID
    content: str = ""
    title: str = ""
    fact_type: str = ""
    fact_key: str = ""
    fact_value: str = ""
    polarity: str = ""


def tokenize(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms = set(normalized.split())
    for match in _CHINESE.finditer(normalized):
        characters = match.group()
        terms.update(characters[i : i + 2] for i in range(len(characters) - 1))
    return frozenset(terms)


def rank_keyword(
    query: str, documents: Sequence[KeywordDocument]
) -> tuple[RankedScore, ...]:
    terms = tokenize(query)
    if not terms:
        return ()
    scores = []
    for document in documents:
        facts = " ".join(
            (
                document.fact_type,
                document.fact_key,
                document.fact_value,
                document.polarity,
            )
        )
        score = (
            len(terms & tokenize(document.content))
            + 3 * len(terms & tokenize(document.title))
            + 2 * len(terms & tokenize(facts))
        )
        scores.append((document.chunk_id, float(score)))
    scores.sort(key=lambda row: (-row[1], row[0]))
    return tuple(
        RankedScore(chunk_id, rank, score)
        for rank, (chunk_id, score) in enumerate(scores, 1)
    )
