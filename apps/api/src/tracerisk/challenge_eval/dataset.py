"""Read frozen JSONL without changing its bytes or synthesizing gold labels."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from tracerisk.ai.claims import CLAIM_CATALOG
from tracerisk.ai.schemas import ContentHash
from tracerisk.challenge_eval.schemas import ChallengeCase, StrictModel


class LoadedDataset(StrictModel):
    cases: list[ChallengeCase]
    sha256: ContentHash


def parse_cases(raw: bytes) -> LoadedDataset:
    cases = [
        ChallengeCase.model_validate_json(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    ]
    case_ids: set[str] = set()
    chunk_ids: set[UUID] = set()
    for case in cases:
        if case.case_id in case_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        case_ids.add(case.case_id)
        local_ids = {chunk.chunk_id for chunk in case.chunks}
        for chunk in case.chunks:
            if chunk.chunk_id in chunk_ids:
                raise ValueError(f"duplicate chunk_id: {chunk.chunk_id}")
            chunk_ids.add(chunk.chunk_id)
            if sha256(chunk.content.encode("utf-8")).hexdigest() != chunk.content_hash:
                raise ValueError(f"content_hash mismatch: {chunk.chunk_id}")
        for field in ("supporting_chunk_ids", "distractor_chunk_ids"):
            references: list[UUID] = getattr(case, field)
            if not set(references) <= local_ids:
                raise ValueError(f"{case.case_id}: missing {field} reference")
        if set(case.supporting_chunk_ids) & set(case.distractor_chunk_ids):
            raise ValueError(f"{case.case_id}: support/distractor overlap")
        if not set(case.allowed_claim_codes) <= CLAIM_CATALOG.keys():
            raise ValueError(f"{case.case_id}: unknown claim code")
    return LoadedDataset(cases=cases, sha256=sha256(raw).hexdigest())


def load_cases(path: Path) -> LoadedDataset:
    return parse_cases(path.read_bytes())


def validate_frozen_shape(dataset: LoadedDataset) -> None:
    expected = {
        "explicit_evidence": 10,
        "similar_entity_or_number": 10,
        "source_conflict": 10,
        "insufficient_evidence": 10,
    }
    if (
        len(dataset.cases) != 40
        or Counter(c.category for c in dataset.cases) != expected
    ):
        raise ValueError("frozen dataset must contain 40 cases and 10 per category")
