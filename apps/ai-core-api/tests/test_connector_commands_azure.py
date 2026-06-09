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


def test_microsoft_admin_device_scopes_are_single_resource_profiles():
    assert azure_commands.microsoft_admin_scope_profile("graph") == "graph"
    assert azure_commands.microsoft_admin_scope_profile("unknown") == "arm"

    arm_scope = azure_commands.microsoft_admin_device_scope_string("arm").split()
    graph_scope = azure_commands.microsoft_admin_device_scope_string("graph").split()
    exchange_scope = azure_commands.microsoft_admin_device_scope_string("exchange").split()

    assert azure_commands.AZURE_ARM_SCOPE in arm_scope
    assert azure_commands.MICROSOFT_GRAPH_SCOPE in graph_scope
    assert azure_commands.EXCHANGE_ONLINE_SCOPE in exchange_scope
    assert azure_commands.MICROSOFT_GRAPH_SCOPE not in arm_scope
    assert azure_commands.EXCHANGE_ONLINE_SCOPE not in graph_scope
    assert "offline_access" in graph_scope


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


@pytest.mark.asyncio
async def test_ms_admin_azure_cli_mode_delegates_to_user_scoped_azure_cli(monkeypatch):
    called: dict[str, object] = {}

    async def fake_run_azure_cli_command(command, user_id, timeout=60):
        called["command"] = command
        called["user_id"] = user_id
        called["timeout"] = timeout
        return {"status": "success", "connector": "azure_cli", "stdout": "[]"}

    monkeypatch.setattr(azure_commands, "run_azure_cli_command", fake_run_azure_cli_command)
    user_id = uuid.uuid4()

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "azure_cli", "command": "account show", "timeout": 30},
        user_id,
    )

    assert called == {"command": "account show", "user_id": user_id, "timeout": 30}
    assert result["status"] == "success"
    assert result["connector"] == "ms_admin"
    assert result["mode"] == "azure_cli"


@pytest.mark.asyncio
async def test_ms_admin_rejects_github_commands_from_powershell_mode(monkeypatch):
    async def unexpected_token_lookup(*_args, **_kwargs):
        raise AssertionError("token lookup should not run for rejected GitHub command")

    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token", unexpected_token_lookup)

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "powershell", "script": "gh run list --repo owner/repo"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "unsupported_command"
    assert "GitHub connector" in result["error"]
