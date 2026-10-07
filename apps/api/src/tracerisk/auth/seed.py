import asyncio
import sys

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.auth.models import Role, User
from tracerisk.auth.service import PASSWORD_HASH
from tracerisk.config import Settings

DEMO_ACCOUNTS = (
    ("analyst", "AnalystDemo!2026", Role.ANALYST),
    ("reviewer", "ReviewerDemo!2026", Role.REVIEWER),
    ("admin", "AdminDemo!2026", Role.ADMIN),
)


async def seed_accounts(session: AsyncSession) -> None:
    for username, password, role in DEMO_ACCOUNTS:
        password_hash = await asyncio.to_thread(PASSWORD_HASH.hash, password)
        await session.execute(
            insert(User)
            .values(username=username, password_hash=password_hash, role=role.value)
            .on_conflict_do_nothing(index_elements=[User.username])
        )
    await session.commit()


async def main() -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            await seed_accounts(session)
        print("Demo accounts initialized; existing accounts preserved.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(main())
