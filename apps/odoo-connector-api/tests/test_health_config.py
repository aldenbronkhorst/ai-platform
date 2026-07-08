"""F9: the /health readiness endpoint reports degraded/503 on a misconfigured
deploy (so a broken instance does not silently take traffic)."""
import os

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_degraded_503_when_debug_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "true")
    get_settings.cache_clear()
    try:
        resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert any(i.get("check") == "DEBUG" for i in data.get("config_issues", []))
    finally:
        get_settings.cache_clear()


def test_health_degraded_when_internal_api_key_missing(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    get_settings.cache_clear()
    try:
        resp = client.get("/health")
        assert resp.status_code == 503
        assert any(i.get("check") == "INTERNAL_API_KEY" for i in resp.json().get("config_issues", []))
    finally:
        get_settings.cache_clear()
