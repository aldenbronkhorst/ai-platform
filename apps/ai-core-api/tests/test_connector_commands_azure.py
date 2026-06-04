import base64
import json
import time
import uuid

import msal
import pytest

from app.services import connector_commands as azure_commands


def _base64url_json(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwt(claims: dict) -> str:
    return f"{_base64url_json({'alg': 'none', 'typ': 'JWT'})}.{_base64url_json(claims)}.signature"


def test_azure_device_scope_matches_msal_device_auth_shape():
    scopes = azure_commands.azure_device_scope_string().split()

    assert azure_commands.AZURE_ARM_SCOPE in scopes
    assert "openid" in scopes
    assert "profile" in scopes
    assert "offline_access" in scopes
    assert azure_commands.azure_token_request_data()["client_info"] == "1"


def test_extract_azure_username_does_not_use_old_fake_fallback():
    assert azure_commands.extract_azure_username({"username": "azure-user"}) == ""

    claims = {
        "aud": azure_commands.AZURE_CLI_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "sub": "subject-id",
        "tid": azure_commands.TENANT_ID,
        "upn": "alden@example.com",
    }
    assert azure_commands.extract_azure_username({"access_token": _jwt(claims)}) == "alden@example.com"


def test_write_azure_cli_files_creates_account_matching_profile_username(tmp_path):
    user_name = "alden@example.com"
    claims = {
        "aud": azure_commands.AZURE_CLI_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "oid": "00000000-0000-0000-0000-000000000001",
        "preferred_username": user_name,
        "sub": "subject-id",
        "tid": azure_commands.TENANT_ID,
    }
    token_data = {
        "client_id": azure_commands.AZURE_CLI_CLIENT_ID,
        "token_type": "Bearer",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "id_token": _jwt(claims),
        "client_info": _base64url_json({"uid": "uid-value", "utid": azure_commands.TENANT_ID}),
        "scope": azure_commands.azure_device_scope_string(),
        "expires_in": 3600,
        "expires_on": int(time.time()) + 3600,
        "username": user_name,
    }
    subscriptions = [
        {
            "subscriptionId": "11111111-1111-1111-1111-111111111111",
            "displayName": "Production",
            "state": "Enabled",
            "tenantId": azure_commands.TENANT_ID,
        }
    ]

    azure_commands._write_azure_cli_files(str(tmp_path), token_data, user_name, subscriptions)

    profile = json.loads((tmp_path / "azureProfile.json").read_text(encoding="utf-8"))
    assert profile["subscriptions"][0]["user"]["name"] == user_name

    cache = msal.SerializableTokenCache()
    cache.deserialize((tmp_path / "msal_token_cache.json").read_text(encoding="utf-8"))
    accounts = list(
        cache.search(
            cache.CredentialType.ACCOUNT,
            query={"environment": "login.microsoftonline.com"},
        )
    )
    assert len(accounts) == 1
    assert accounts[0]["username"] == user_name


@pytest.mark.asyncio
async def test_ensure_azure_cli_profile_rejects_tokens_without_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_CLI_USER_CONFIG_ROOT", str(tmp_path))
    result = await azure_commands.ensure_azure_cli_profile(
        uuid.uuid4(),
        {"access_token": "access-token", "username": "azure-user"},
        subscriptions_result={"ok": True, "subscriptions": []},
    )

    assert result["ready"] is False
    assert "no usable user identity" in result["message"]


@pytest.mark.asyncio
async def test_validate_azure_cli_profile_forces_msal_token_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_CLI_USER_CONFIG_ROOT", str(tmp_path))
    called: dict[str, object] = {}

    class Result:
        success = True
        stdout = "{}"

        def to_dict(self):
            return {"stderr": "", "exit_code": 0, "error": None}

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["command"] = command
        called["timeout"] = timeout
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(azure_commands, "run_command", fake_run_command)

    result = await azure_commands.validate_azure_cli_profile(uuid.uuid4())

    assert result["ready"] is True
    assert "account get-access-token" in str(called["command"])
    assert "account show" not in str(called["command"])
    assert called["allowed_binaries"] == azure_commands.AZURE_ALLOWED_BINARIES
