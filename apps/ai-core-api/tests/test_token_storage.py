import json
import time
from uuid import UUID

import pytest

from app.services import token_storage


def test_token_status_from_data_reports_refreshed_connected_token():
    status = token_storage.token_status_from_data("azure", {
        "token_type": "Bearer",
        "access_token": "fresh-token",
        "expires_on": int(time.time()) + 3600,
        "scope": "scope",
        "username": "alden@example.com",
    })

    assert status["status"] == "connected"
    assert status["provider"] == "azure"
    assert status["username"] == "alden@example.com"


def test_token_status_from_data_reports_expired_token():
    status = token_storage.token_status_from_data("azure", {
        "access_token": "old-token",
        "expires_on": int(time.time()) - 1,
    })

    assert status["status"] == "expired"


@pytest.mark.asyncio
async def test_store_token_recovers_soft_deleted_secret(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")
    secret_name = token_storage.token_secret_name("azure", user_id)
    calls: list[tuple[str, str, str | None]] = []

    class RecoverableConflict(Exception):
        error_code = "ObjectIsDeletedButRecoverable"

    async def fake_set_secret_value(name: str, value: str) -> None:
        calls.append(("set", name, json.loads(value)["access_token"]))
        if len([call for call in calls if call[0] == "set"]) == 1:
            raise RecoverableConflict("Secret is currently in a deleted but recoverable state.")

    async def fake_recover_deleted_secret(name: str) -> None:
        calls.append(("recover", name, None))

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)
    monkeypatch.setattr(token_storage, "recover_deleted_secret", fake_recover_deleted_secret)

    stored = await token_storage.store_token("azure", user_id, {"access_token": "new-token"})

    assert stored is True
    assert calls == [
        ("set", secret_name, "new-token"),
        ("recover", secret_name, None),
        ("set", secret_name, "new-token"),
    ]


@pytest.mark.asyncio
async def test_store_token_does_not_recover_unrelated_write_failures(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")

    async def fake_set_secret_value(name: str, value: str) -> None:
        raise RuntimeError("Forbidden")

    async def fake_recover_deleted_secret(name: str) -> None:
        raise AssertionError("recover should not be called")

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)
    monkeypatch.setattr(token_storage, "recover_deleted_secret", fake_recover_deleted_secret)

    stored = await token_storage.store_token("azure", UUID("e4807f22-97c8-4000-8000-000000000001"), {})

    assert stored is False
