import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.evaluation import (
    evaluate,
    load_cases,
    recall_at_5,
    report_bytes,
    source_snapshot_hash,
)
from tracerisk.retrieval.indexing import IndexContract, IndexReadinessError, build_index
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog

DATASET = (
    Path(__file__).resolve().parents[4] / "datasets/evaluation/retrieval_cases.jsonl"
)


def test_static_labels_cover_required_domains_and_edge_cases():
    cases = load_cases(DATASET)
    assert len(cases) == 16
    assert {c.risk_type for c in cases} == {
        "business",
        "judicial",
        "news",
        "intellectual_property",
    }
    assert {tag for c in cases for tag in c.tags} >= {
        "aliases",
        "homonym",
        "duplicate_news",
        "date_boundaries",
        "conflicts",
        "prompt_injection",
    }
    assert source_snapshot_hash() == source_snapshot_hash()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_case",
        "unordered_cases",
        "unknown_company",
        "unknown_risk",
        "empty_relevant",
        "unknown_evidence",
        "other_company",
        "other_domain",
        "future_relevant",
        "duplicate_relevant",
        "unordered_relevant",
        "blank_query",
        "empty_dataset",
        "blank_line",
        "extra_field",
        "unordered_tags",
    ],
)
def test_reject_invalid_labels(tmp_path, mutation):
    rows = [json.loads(line) for line in DATASET.read_text("utf-8").splitlines()]
    if mutation == "duplicate_case":
        rows.insert(0, rows[0])
    elif mutation == "unordered_cases":
        rows.reverse()
    elif mutation == "unknown_company":
        rows[0]["company_id"] = str(UUID(int=999))
    elif mutation == "unknown_risk":
        rows[0]["risk_type"] = "unknown"
    elif mutation == "empty_relevant":
        rows[0]["relevant_evidence_ids"] = []
    elif mutation == "unknown_evidence":
        rows[0]["relevant_evidence_ids"] = [str(UUID(int=999))]
    elif mutation == "other_company":
        rows[0]["company_id"] = rows[1]["company_id"]
    elif mutation == "other_domain":
        rows[0]["risk_type"] = "news"
    elif mutation == "future_relevant":
        rows[0]["as_of_date"] = "2000-01-01"
    elif mutation == "duplicate_relevant":
        rows[0]["relevant_evidence_ids"] *= 2
    elif mutation == "unordered_relevant":
        rows[1]["relevant_evidence_ids"].reverse()
    elif mutation == "blank_query":
        rows[0]["query"] = " "
    elif mutation == "empty_dataset":
        rows = []
    elif mutation == "extra_field":
        rows[0]["answer"] = "Do not allow generated answers"
    elif mutation == "unordered_tags":
        rows[0]["tags"] = ["z", "a"]
    payload = "\n".join(json.dumps(row) for row in rows)
    if mutation == "blank_line":
        payload += "\n\n"
    path = tmp_path / "cases.jsonl"
    path.write_text(payload, "utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


def test_recall_top5_cap_dedup_and_partial_score():
    ids = [UUID(int=n) for n in range(1, 8)]
    assert recall_at_5(ids[:2], [ids[0], ids[0]]) == 0.5
    assert recall_at_5(ids[5:], ids) == 0.0
    assert recall_at_5(ids, ids) == 5 / 7
    with pytest.raises(ValueError):
        recall_at_5([], ids)
    assert report_bytes({"b": 1, "a": 2}) == report_bytes({"a": 2, "b": 1})


@pytest.mark.anyio
async def test_same_pipeline_deterministic_report_and_matching_query_provider(database):
    class RecordingFake(FakeEmbedding):
        def __init__(self):
            self.queries = []

        async def embed_queries(self, texts):
            self.queries.extend(texts)
            return await super().embed_queries(texts)

    engine = create_async_engine(database.database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            await import_catalog(session, generate_catalog())
            user = User(username="evaluation", password_hash="disabled", role="analyst")
            session.add(user)
            await session.flush()
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            provider = RecordingFake()
            first = await evaluate(
                session, dataset_path=DATASET, provider=provider, principal_id=user.id
            )
            second = await evaluate(
                session, dataset_path=DATASET, provider=provider, principal_id=user.id
            )
            assert report_bytes(first) == report_bytes(second)
            assert provider.queries == [c.query for c in load_cases(DATASET)] * 2
            assert first["overall_recall_at_5"] == 1.0
            assert first["scope_probes"][0]["passed"] is True
            assert first["scope_probes"][0]["excluded_from_recall"] is True
            provider.model = "wrong-model"
            with pytest.raises(IndexReadinessError):
                await evaluate(
                    session,
                    dataset_path=DATASET,
                    provider=provider,
                    principal_id=user.id,
                )
            assert len(provider.queries) == 32  # compatibility before embedding
            provider.model = FakeEmbedding.model
            with pytest.raises(PermissionError):
                await evaluate(
                    session,
                    dataset_path=DATASET,
                    provider=provider,
                    principal_id=UUID(int=999),
                )
            session.add(
                Company(
                    name="Extra", synthetic_key="extra", unified_credit_code="extra"
                )
            )
            await session.flush()
            with pytest.raises(ValueError, match="extra source"):
                await evaluate(
                    session,
                    dataset_path=DATASET,
                    provider=provider,
                    principal_id=user.id,
                )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_evaluation_rejects_unseeded_database(database):
    engine = create_async_engine(database.database_url)
    try:
        with pytest.raises(ValueError, match="complete preloaded"):
            async with async_sessionmaker(engine).begin() as session:
                await evaluate(
                    session,
                    dataset_path=DATASET,
                    provider=FakeEmbedding(),
                    principal_id=UUID(int=1),
                )
    finally:
        await engine.dispose()
