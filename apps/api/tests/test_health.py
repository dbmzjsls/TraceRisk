from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from tracerisk.main import app


def test_health_without_database(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "1")
    # The endpoint remains liveness-only; startup separately validates the index.
    monkeypatch.setattr("tracerisk.main.check_index", AsyncMock(return_value=None))
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
