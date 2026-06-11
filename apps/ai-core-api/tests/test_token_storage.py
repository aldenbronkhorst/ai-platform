import json
import time
from uuid import UUID

import pytest

from app.services import token_storage


def test_token_secret_name_sanitizes_provider_for_key_vault():
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")

    assert token_storage.token_secret_name("microsoft_admin", user_id) == "connector-token-microsoft-admin-e4807f2297c8"
    assert token_storage.token_secret_name("///", user_id) == "connector-token-connector-e4807f2297c8"


def test_token_status_from_data_reports_refreshed_connected_token():
    status = token_storage.token_status_from_data("microsoft_admin", {
        "token_type": "Bearer",
        "access_token": "fresh-token",
        "expires_on": int(time.time()) + 3600,
        "scope": "scope",
        "username": "alden@example.com",
    })

    assert status["status"] == "connected"
    assert status["provider"] == "microsoft_admin"
    assert status["username"] == "alden@example.com"


def test_token_status_from_data_reports_expired_token():
    status = token_storage.token_status_from_data("microsoft_admin", {
        "access_token": "old-token",
        "expires_on": int(time.time()) - 1,
    })

    assert status["status"] == "expired"


def test_token_status_from_data_reports_refresh_error_without_access_token():
    status = token_storage.token_status_from_data("microsoft_admin", {
        "refresh_error": "Reconnect Microsoft Admin.",
        "error_type": "reconnect_required",
        "username": "alden@example.com",
    })

    assert status["status"] == "error"
    assert status["error_type"] == "reconnect_required"
    assert status["error"] == "Reconnect Microsoft Admin."


def test_token_status_from_data_treats_disconnected_marker_as_not_connected():
    status = token_storage.token_status_from_data("azure_cli", {
        "provider": "azure_cli",
        "status": "disconnected",
        "disconnected_at": int(time.time()),
    })

    assert status == {"status": "not_connected", "provider": "azure_cli"}


def test_token_status_from_data_treats_empty_payload_as_not_connected():
    status = token_storage.token_status_from_data("azure_cli", {"provider": "azure_cli"})

    assert status == {"status": "not_connected", "provider": "azure_cli"}


@pytest.mark.asyncio
async def test_store_token_recovers_soft_deleted_secret(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")
    secret_name = token_storage.token_secret_name("microsoft_admin", user_id)
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

    stored = await token_storage.store_token("microsoft_admin", user_id, {"access_token": "new-token"})

    assert stored is True
    assert calls == [
        ("set", secret_name, "new-token"),
        ("recover", secret_name, None),
        ("set", secret_name, "new-token"),
    ]


@pytest.mark.asyncio
async def test_store_token_compacts_large_microsoft_admin_payload(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")
    captured: dict[str, str] = {}

    async def fake_set_secret_value(name: str, value: str) -> None:
        captured["name"] = name
        captured["value"] = value

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)

    stored = await token_storage.store_token(
        "microsoft_admin",
        user_id,
        {
            "client_id": "microsoft-admin-client-id",
            "token_type": "Bearer",
            "access_token": "graph-access-token",
            "refresh_token": "shared-refresh-token",
            "scope": "https://graph.microsoft.com/User.Read offline_access",
            "scope_profile": "graph",
            "username": "alden@example.com",
            "id_token": "x" * 8_000,
            "id_token_claims": {"name": "Alden", "blob": "y" * 8_000},
            "client_info": "z" * 2_000,
            "consented_scope_profiles": ["graph", "arm", "exchange"],
            "delegated_tokens": {
                "graph": {
                    "access_token": "graph-access-token",
                    "refresh_token": "graph-refresh-token",
                    "scope_profile": "graph",
                    "id_token": "x" * 8_000,
                    "client_info": "z" * 2_000,
                },
                "arm": {
                    "client_id": "microsoft-admin-client-id",
                    "token_type": "Bearer",
                    "access_token": "arm-access-token",
                    "refresh_token": "arm-refresh-token",
                    "scope": "https://management.azure.com/.default",
                    "scope_profile": "arm",
                    "id_token": "x" * 8_000,
                    "id_token_claims": {"blob": "y" * 8_000},
                    "client_info": "z" * 2_000,
                    "expires_on": 4_102_444_800,
                },
                "exchange": {
                    "client_id": "microsoft-admin-client-id",
                    "token_type": "Bearer",
                    "access_token": "exchange-access-token",
                    "refresh_token": "exchange-refresh-token",
                    "scope": "https://outlook.office365.com/.default",
                    "scope_profile": "exchange",
                    "id_token": "x" * 8_000,
                    "id_token_claims": {"blob": "y" * 8_000},
                    "client_info": "z" * 2_000,
                    "expires_on": 4_102_444_800,
                },
            },
        },
    )

    assert stored is True
    payload = json.loads(captured["value"])
    assert len(captured["value"]) < 25_600
    assert payload["provider"] == "microsoft_admin"
    assert payload["refresh_token"] == "shared-refresh-token"
    assert payload["consented_scope_profiles"] == ["graph", "arm", "exchange"]
    assert "id_token" not in payload
    assert "id_token_claims" not in payload
    assert payload["client_info"] == "z" * 2_000
    assert "graph" not in payload["delegated_tokens"]
    assert payload["delegated_tokens"]["arm"]["access_token"] == "arm-access-token"
    assert payload["delegated_tokens"]["arm"]["client_info"] == "z" * 2_000
    assert payload["delegated_tokens"]["exchange"]["access_token"] == "exchange-access-token"
    assert "refresh_token" not in payload["delegated_tokens"]["arm"]
    assert "id_token" not in payload["delegated_tokens"]["exchange"]


@pytest.mark.asyncio
async def test_store_token_compacts_native_microsoft_provider_payload(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")
    captured: dict[str, str] = {}

    async def fake_set_secret_value(name: str, value: str) -> None:
        captured["name"] = name
        captured["value"] = value

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)

    stored = await token_storage.store_token(
        "microsoft_graph",
        user_id,
        {
            "client_id": "graph-powershell-client-id",
            "token_type": "Bearer",
            "access_token": "graph-access-token",
            "refresh_token": "graph-refresh-token",
            "scope": "https://graph.microsoft.com/User.Read offline_access",
            "scope_profile": "graph",
            "username": "alden@example.com",
            "id_token": "x" * 8_000,
            "id_token_claims": {"name": "Alden", "blob": "y" * 8_000},
            "client_info": "z" * 2_000,
            "delegated_tokens": {
                "graph": {
                    "access_token": "graph-access-token",
                    "refresh_token": "graph-refresh-token",
                    "scope_profile": "graph",
                    "id_token": "x" * 8_000,
                    "client_info": "z" * 2_000,
                },
            },
        },
    )

    assert stored is True
    payload = json.loads(captured["value"])
    assert len(captured["value"]) < 25_600
    assert payload["provider"] == "microsoft_graph"
    assert payload["access_token"] == "graph-access-token"
    assert payload["refresh_token"] == "graph-refresh-token"
    assert "id_token" not in payload
    assert "id_token_claims" not in payload
    assert "delegated_tokens" not in payload


@pytest.mark.asyncio
async def test_store_token_does_not_recover_unrelated_write_failures(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")

    async def fake_set_secret_value(name: str, value: str) -> None:
        raise RuntimeError("Forbidden")

    async def fake_recover_deleted_secret(name: str) -> None:
        raise AssertionError("recover should not be called")

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)
    monkeypatch.setattr(token_storage, "recover_deleted_secret", fake_recover_deleted_secret)

    stored = await token_storage.store_token("microsoft_admin", UUID("e4807f22-97c8-4000-8000-000000000001"), {})

    assert stored is False


@pytest.mark.asyncio
async def test_delete_token_clears_secret_instead_of_soft_deleting(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")
    captured: dict[str, str] = {}

    async def fake_set_secret_value(name: str, value: str) -> None:
        captured["name"] = name
        captured["value"] = value

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)

    deleted = await token_storage.delete_token("azure_cli", user_id)

    assert deleted is True
    assert captured["name"] == token_storage.token_secret_name("azure_cli", user_id)
    payload = json.loads(captured["value"])
    assert payload["provider"] == "azure_cli"
    assert payload["status"] == "disconnected"
    assert "access_token" not in payload
    assert "refresh_token" not in payload


@pytest.mark.asyncio
async def test_delete_token_recovers_soft_deleted_secret_before_clearing(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    user_id = UUID("e4807f22-97c8-4000-8000-000000000001")
    secret_name = token_storage.token_secret_name("azure_cli", user_id)
    calls: list[tuple[str, str, str | None]] = []

    class RecoverableConflict(Exception):
        error_code = "ObjectIsDeletedButRecoverable"

    async def fake_set_secret_value(name: str, value: str) -> None:
        calls.append(("set", name, json.loads(value)["status"]))
        if len([call for call in calls if call[0] == "set"]) == 1:
            raise RecoverableConflict("Secret is currently in a deleted but recoverable state.")

    async def fake_recover_deleted_secret(name: str) -> None:
        calls.append(("recover", name, None))

    monkeypatch.setattr(token_storage, "set_secret_value", fake_set_secret_value)
    monkeypatch.setattr(token_storage, "recover_deleted_secret", fake_recover_deleted_secret)

    deleted = await token_storage.delete_token("azure_cli", user_id)

    assert deleted is True
    assert calls == [
        ("set", secret_name, "disconnected"),
        ("recover", secret_name, None),
        ("set", secret_name, "disconnected"),
    ]


@pytest.mark.asyncio
async def test_retrieve_token_ignores_disconnected_marker(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")

    async def fake_get_secret_value(name: str) -> str:
        return json.dumps({"provider": "azure_cli", "status": "disconnected"})

    monkeypatch.setattr(token_storage, "get_secret_value", fake_get_secret_value)

    token = await token_storage.retrieve_token("azure_cli", UUID("e4807f22-97c8-4000-8000-000000000001"))

    assert token is None
