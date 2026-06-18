"""FastAPI surface tests: /health and the webhook token guard."""

from __future__ import annotations

from fastapi.testclient import TestClient

from dbaylo import __version__
from dbaylo.web.app import create_app

client = TestClient(create_app())


def test_health_returns_200() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_webhook_rejects_wrong_token() -> None:
    # No BOT_TOKEN configured in tests -> any token is rejected (fail closed).
    response = client.post("/webhook/not-the-token", json={"update_id": 1})
    assert response.status_code == 403


def test_webhook_rejects_empty_token_path() -> None:
    response = client.post("/webhook/", json={"update_id": 1})
    assert response.status_code in (404, 405)
