"""Evaluate a separately provisioned, explicitly indexed synthetic database."""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.auth.models import User
from tracerisk.config import Settings
from tracerisk.retrieval.embeddings import BGEEmbedding, FakeEmbedding
from tracerisk.retrieval.evaluation import evaluate, report_bytes

ROOT = Path(__file__).resolve().parents[1]


async def main(provider_name: str, dataset: Path, output: Path) -> None:
    settings = Settings()
    # Avoid accidentally seeding or evaluating against preserved application data.
    if not settings.postgres_db.startswith("tracerisk_eval_"):
        raise ValueError("Use an isolated tracerisk_eval_ database")
    provider = FakeEmbedding() if provider_name == "fake" else BGEEmbedding()
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            user = await session.scalar(
                select(User).where(User.username == "evaluation")
            )
            if user is None:
                user = User(
                    username="evaluation", password_hash="disabled", role="analyst"
                )
                session.add(user)
                await session.flush()
            report = await evaluate(
                session, dataset_path=dataset, provider=provider, principal_id=user.id
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(report_bytes(report))
        print(f"{provider_name}: Recall@5={report['overall_recall_at_5']}; {output}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fake", "bge"), required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "datasets/evaluation/retrieval_cases.jsonl",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(main(args.provider, args.dataset, args.output))
