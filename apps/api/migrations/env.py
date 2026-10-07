from alembic import context
from sqlalchemy import create_engine, pool

from tracerisk.ai import models as ai_models  # noqa: F401
from tracerisk.audit import models as audit_models  # noqa: F401
from tracerisk.auth import models as auth_models  # noqa: F401
from tracerisk.companies import models as company_models  # noqa: F401
from tracerisk.config import Settings
from tracerisk.db.base import Base
from tracerisk.evidence import models as evidence_models  # noqa: F401
from tracerisk.investigations import models  # noqa: F401
from tracerisk.reports import models as report_models  # noqa: F401
from tracerisk.retrieval import models as retrieval_models  # noqa: F401
from tracerisk.reviews import models as review_models  # noqa: F401
from tracerisk.rules import models as rule_models  # noqa: F401

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str]) -> bool:
    # Procrastinate owns and migrates its own tables.
    return not (
        type_ == "table"
        and name is not None
        and (
            name.startswith("procrastinate_")
            or name
            in {
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
                "checkpoint_migrations",
            }
        )
    )


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The migration CLI is synchronous, separate from the async API event loop.
    engine = create_engine(Settings().database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_name=include_name,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
