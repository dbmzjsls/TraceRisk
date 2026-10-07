"""Explicit operator command: build and activate an index in one transaction."""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.config import Settings
from tracerisk.retrieval.embeddings import BGEEmbedding, FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index


async def run(provider_name: str) -> None:
    settings = Settings()
    provider = FakeEmbedding() if provider_name == "fake" else BGEEmbedding()
    contract = IndexContract(
        provider.provider,
        provider.model,
        settings.embedding_dimensions,
        settings.embedding_distance,
        settings.chunk_schema_version,
    )
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            version = await build_index(session, provider, contract)
            key = version.version_key
        print(f"Active index: {key}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fake", "bge"), required=True)
    args = parser.parse_args()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(run(args.provider))


if __name__ == "__main__":
    main()
