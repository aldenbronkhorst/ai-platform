"""Auth hardening tests.

Covers the audit fixes:
  F4 - APP_ENV=test must not grant unauthenticated access outside pytest.
  F5 - Entra app-role enforcement gates auto-provisioning when enabled.
  F6 - the internal API key is compared in constant time and rejects mismatches.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import _authorized_by_app_role, _running_under_pytest
from app.main import app

client = TestClient(app)
USER_HEADER = {"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
STATUS_URL = "/connected-accounts/odoo/status"


# ── F4: test-mode auth must be inert outside the pytest harness ──

def test_running_under_pytest_is_true_in_tests():
    assert _running_under_pytest() is True


def test_test_mode_auth_disabled_when_not_under_pytest():
    """With APP_ENV=test but pytest not present, the test-auth path must not grant access."""
    with patch("app.core.security._running_under_pytest", return_value=False):
        resp = client.get(STATUS_URL, headers=USER_HEADER)
    assert resp.status_code == 401


def test_startup_config_flags_test_mode_with_key_vault(monkeypatch):
    """APP_ENV=test alongside a configured Key Vault is reported as a startup failure."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("KEY_VAULT_URI", "https://example.vault.azure.net")
    get_settings.cache_clear()
    try:
        data = client.get("/health").json()
        app_env_issues = [i for i in data.get("config_issues", []) if i.get("check") == "APP_ENV"]
        assert app_env_issues, "expected an APP_ENV startup issue"
        assert "deployed" in app_env_issues[0]["message"].lower()
    finally:
        get_settings.cache_clear()


# ── F6: constant-time internal API key check ──

def test_wrong_internal_api_key_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "correct-secret")
    get_settings.cache_clear()
    try:
        resp = client.get(STATUS_URL, headers={"X-API-Key": "wrong-secret"})
        assert resp.status_code == 401
    finally:
        get_settings.cache_clear()


def test_correct_internal_api_key_is_accepted(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "correct-secret")
    get_settings.cache_clear()
    try:
        resp = client.get(STATUS_URL, headers={"X-API-Key": "correct-secret"})
        # Auth must succeed (any non-401); endpoint returns a status payload.
        assert resp.status_code != 401
    finally:
        get_settings.cache_clear()


def test_empty_configured_api_key_never_authenticates(monkeypatch):
    """An unset server API key must not allow an empty/any client key through."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    try:
        resp = client.get(STATUS_URL, headers={"X-API-Key": ""})
        assert resp.status_code == 401
    finally:
        get_settings.cache_clear()


def test_non_ascii_api_key_is_rejected_cleanly(monkeypatch):
    """A non-ASCII X-API-Key must return 401, not crash with 500.

    Starlette latin-1-decodes header bytes and hmac.compare_digest raises
    TypeError on non-ASCII str operands; the byte-wise comparison must handle
    this and fall through to a clean 401.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "correct-secret")
    get_settings.cache_clear()
    try:
        # Send raw non-ASCII bytes on the wire (as curl would); Starlette
        # latin-1-decodes them server-side. A str value would be rejected by the
        # client before it left, so this must be bytes to exercise the server.
        resp = client.get(STATUS_URL, headers={"X-API-Key": b"\xe9"})
        assert resp.status_code == 401
    finally:
        get_settings.cache_clear()


# ── F5: Entra app-role enforcement (pure decision function) ──

def test_app_role_not_enforced_by_default():
    get_settings.cache_clear()
    assert _authorized_by_app_role([]) is True
    assert _authorized_by_app_role(None) is True
    assert _authorized_by_app_role(["anything"]) is True


def test_app_role_required_when_enforcement_enabled(monkeypatch):
    monkeypatch.setenv("REQUIRE_APP_ROLE", "true")
    get_settings.cache_clear()
    try:
        assert _authorized_by_app_role([]) is False
        assert _authorized_by_app_role(None) is False
        assert _authorized_by_app_role(["AIPlatform.User"]) is True
        assert _authorized_by_app_role(["aiplatform.admin"]) is True
        assert _authorized_by_app_role(["SomeOtherRole"]) is False
    finally:
        get_settings.cache_clear()
