"""Application readiness uses local migration metadata without database access."""

from unittest.mock import MagicMock

import pytest
from scripts import database_lifecycle as lifecycle

from tracerisk.config import Settings


def test_application_head_reads_local_alembic_migrations() -> None:
    assert lifecycle.application_head() == "0012"


@pytest.mark.parametrize("heads", [[], ["0012", "other"]])
def test_application_head_rejects_missing_or_multiple_heads(
    monkeypatch: pytest.MonkeyPatch, heads: list[str]
) -> None:
    scripts = MagicMock()
    scripts.get_heads.return_value = heads
    monkeypatch.setattr(lifecycle.ScriptDirectory, "from_config", lambda _: scripts)
    with pytest.raises(RuntimeError, match="exactly one Alembic head"):
        lifecycle.application_head()


@pytest.fixture
def ready_target(monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, MagicMock]:
    settings = Settings(
        _env_file=None,
        model_adapter="mock",
        embedding_provider="fake",
        embedding_model="fake-sha256-v1",
    )
    connection = MagicMock()
    connection.__enter__.return_value = connection
    revision = MagicMock()

    def execute(query: object, *_: object) -> MagicMock:
        if query == "SELECT version_num FROM alembic_version":
            return revision
        result = MagicMock()
        result.fetchone.return_value = (
            ("fake", "fake-sha256-v1")
            if query
            == "SELECT provider, model FROM embedding_index_versions WHERE active"
            else (1,)
        )
        return result

    connection.execute.side_effect = execute
    monkeypatch.setattr(lifecycle.psycopg, "connect", lambda _: connection)
    monkeypatch.setattr(lifecycle, "current_database", lambda _, target: target)
    monkeypatch.setattr(lifecycle, "application_head", lambda: "future-head")
    return settings, revision


def test_verify_target_uses_dynamic_application_head(
    ready_target: tuple[Settings, MagicMock],
) -> None:
    settings, revision = ready_target
    revision.fetchall.return_value = [("future-head",)]
    assert lifecycle.verify_target(settings, "tracerisk_demo") == {
        "database": "tracerisk_demo",
        "alembic_revision": "future-head",
        "index": "fake",
    }


@pytest.mark.parametrize(
    "revisions", [[], [("0011",)], [("0012",)], [("future-head",), ("other",)]]
)
def test_verify_target_rejects_missing_wrong_or_multiple_database_revisions(
    ready_target: tuple[Settings, MagicMock], revisions: list[tuple[str]]
) -> None:
    settings, revision = ready_target
    revision.fetchall.return_value = revisions
    with pytest.raises(RuntimeError, match="Alembic revision is incomplete"):
        lifecycle.verify_target(settings, "tracerisk_demo")
