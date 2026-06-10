import base64
import json
import time
import uuid
from types import SimpleNamespace

import msal
import pytest

from app.services.connectors.microsoft_admin import (
    azure_cli,
    bicep,
    constants,
    diagnostics,
    graph,
    powershell_az,
    powershell_common,
    powershell_exchange,
    powershell_graph,
    powershell_pnp,
    powershell_teams,
    tokens,
)

microsoft_admin_commands = SimpleNamespace(
    AZURE_CLI_ARM_TARGET=constants.AZURE_CLI_ARM_TARGET,
    AZURE_CLI_CLIENT_ID=constants.AZURE_CLI_CLIENT_ID,
    AZURE_ARM_SCOPE=constants.AZURE_ARM_SCOPE,
    EXCHANGE_ONLINE_SCOPE=constants.EXCHANGE_ONLINE_SCOPE,
    MICROSOFT_ADMIN_APP_DISPLAY_NAME=constants.MICROSOFT_ADMIN_APP_DISPLAY_NAME,
    MICROSOFT_ADMIN_CLIENT_ID=constants.MICROSOFT_ADMIN_CLIENT_ID,
    MICROSOFT_GRAPH_SCOPE=constants.MICROSOFT_GRAPH_SCOPE,
    MICROSOFT_GRAPH_SCOPES=constants.MICROSOFT_GRAPH_SCOPES,
    MS_AZURE_CLI_ALLOWED_BINARIES=constants.MS_AZURE_CLI_ALLOWED_BINARIES,
    MS_BICEP_ALLOWED_BINARIES=constants.MS_BICEP_ALLOWED_BINARIES,
    MS_POWERSHELL_ALLOWED_BINARIES=constants.MS_POWERSHELL_ALLOWED_BINARIES,
    TENANT_ID=constants.TENANT_ID,
    _get_fresh_microsoft_admin_token=tokens._get_fresh_microsoft_admin_token,
    _get_fresh_microsoft_admin_token_for_scope=tokens._get_fresh_microsoft_admin_token_for_scope,
    _microsoft_admin_scope_request=tokens._microsoft_admin_scope_request,
    _scope_profile_for_scope=tokens._scope_profile_for_scope,
    _sharepoint_scope_for_url=tokens._sharepoint_scope_for_url,
    _write_azure_cli_files=azure_cli._write_azure_cli_files,
    ensure_azure_cli_profile=azure_cli.ensure_azure_cli_profile,
    extract_microsoft_admin_username=tokens.extract_microsoft_admin_username,
    httpx=tokens.httpx,
    microsoft_admin_app_name_for_scope_profile=constants.microsoft_admin_app_name_for_scope_profile,
    microsoft_admin_arm_device_scope_string=constants.microsoft_admin_arm_device_scope_string,
    microsoft_admin_client_id_for_scope_profile=constants.microsoft_admin_client_id_for_scope_profile,
    microsoft_admin_device_scope_string=constants.microsoft_admin_device_scope_string,
    microsoft_admin_scope_profile=constants.microsoft_admin_scope_profile,
    microsoft_admin_scope_summary=constants.microsoft_admin_scope_summary,
    run_ms_az_powershell_tool=powershell_az.run_ms_az_powershell_tool,
    run_ms_azure_cli_tool=azure_cli.run_ms_azure_cli_tool,
    run_ms_bicep_tool=bicep.run_ms_bicep_tool,
    run_ms_exchange_powershell_tool=powershell_exchange.run_ms_exchange_powershell_tool,
    run_ms_graph_powershell_tool=powershell_graph.run_ms_graph_powershell_tool,
    run_ms_graph_tool=graph.run_ms_graph_tool,
    run_ms_sharepoint_pnp_powershell_tool=powershell_pnp.run_ms_sharepoint_pnp_powershell_tool,
    run_ms_teams_powershell_tool=powershell_teams.run_ms_teams_powershell_tool,
    validate_azure_cli_profile=azure_cli.validate_azure_cli_profile,
)


def _base64url_json(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwt(claims: dict) -> str:
    return f"{_base64url_json({'alg': 'none', 'typ': 'JWT'})}.{_base64url_json(claims)}.signature"


def test_microsoft_admin_arm_device_scope_matches_msal_device_auth_shape():
    scopes = microsoft_admin_commands.microsoft_admin_arm_device_scope_string().split()

    assert microsoft_admin_commands.AZURE_ARM_SCOPE in scopes
    assert "openid" in scopes
    assert "profile" in scopes
    assert "offline_access" in scopes


def test_microsoft_admin_device_scopes_are_single_resource_profiles():
    assert microsoft_admin_commands.microsoft_admin_scope_profile("graph") == "graph"
    assert microsoft_admin_commands.microsoft_admin_scope_profile("unknown") == "graph"

    arm_scope = microsoft_admin_commands.microsoft_admin_device_scope_string("arm").split()
    graph_scope = microsoft_admin_commands.microsoft_admin_device_scope_string("graph").split()
    exchange_scope = microsoft_admin_commands.microsoft_admin_device_scope_string("exchange").split()

    assert microsoft_admin_commands.AZURE_ARM_SCOPE in arm_scope
    assert "https://graph.microsoft.com/User.ReadWrite.All" in graph_scope
    assert "https://graph.microsoft.com/Directory.ReadWrite.All" in graph_scope
    assert microsoft_admin_commands.EXCHANGE_ONLINE_SCOPE in exchange_scope
    assert not set(microsoft_admin_commands.MICROSOFT_GRAPH_SCOPES).intersection(arm_scope)
    assert microsoft_admin_commands.EXCHANGE_ONLINE_SCOPE not in graph_scope
    assert "offline_access" in graph_scope


def test_sharepoint_profile_uses_target_site_scope():
    scope = microsoft_admin_commands._sharepoint_scope_for_url("https://tenant.sharepoint.com/sites/example")

    assert scope == "https://tenant.sharepoint.com/.default"
    assert microsoft_admin_commands._scope_profile_for_scope(scope) == "sharepoint"
    assert microsoft_admin_commands._microsoft_admin_scope_request(scope, "sharepoint") == (
        "https://tenant.sharepoint.com/.default openid profile offline_access"
    )
    assert "target SharePoint site" in microsoft_admin_commands.microsoft_admin_scope_summary("sharepoint")


def test_microsoft_admin_client_id_is_profile_specific(monkeypatch):
    monkeypatch.setattr(constants, "MICROSOFT_ADMIN_CLIENT_ID", "admin-client-id")

    assert microsoft_admin_commands.microsoft_admin_client_id_for_scope_profile("arm") == "admin-client-id"
    assert microsoft_admin_commands.microsoft_admin_client_id_for_scope_profile("graph") == "admin-client-id"
    assert microsoft_admin_commands.microsoft_admin_client_id_for_scope_profile("exchange") == "admin-client-id"
    assert microsoft_admin_commands.microsoft_admin_app_name_for_scope_profile("arm") == microsoft_admin_commands.MICROSOFT_ADMIN_APP_DISPLAY_NAME
    assert microsoft_admin_commands.microsoft_admin_app_name_for_scope_profile("graph") == microsoft_admin_commands.MICROSOFT_ADMIN_APP_DISPLAY_NAME


def test_extract_microsoft_admin_username_does_not_use_old_fake_fallback():
    assert microsoft_admin_commands.extract_microsoft_admin_username({"username": "azure-user"}) == ""

    claims = {
        "aud": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "sub": "subject-id",
        "tid": microsoft_admin_commands.TENANT_ID,
        "upn": "alden@example.com",
    }
    assert microsoft_admin_commands.extract_microsoft_admin_username({"access_token": _jwt(claims)}) == "alden@example.com"


def test_write_azure_cli_files_creates_account_matching_profile_username(tmp_path):
    user_name = "alden@example.com"
    claims = {
        "aud": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "oid": "00000000-0000-0000-0000-000000000001",
        "preferred_username": user_name,
        "sub": "subject-id",
        "tid": microsoft_admin_commands.TENANT_ID,
    }
    token_data = {
        "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "token_type": "Bearer",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "id_token": _jwt(claims),
        "client_info": _base64url_json({"uid": "uid-value", "utid": microsoft_admin_commands.TENANT_ID}),
        "scope": microsoft_admin_commands.microsoft_admin_arm_device_scope_string(),
        "expires_in": 3600,
        "expires_on": int(time.time()) + 3600,
        "username": user_name,
    }
    subscriptions = [
        {
            "subscriptionId": "11111111-1111-1111-1111-111111111111",
            "displayName": "Production",
            "state": "Enabled",
            "tenantId": microsoft_admin_commands.TENANT_ID,
        }
    ]

    microsoft_admin_commands._write_azure_cli_files(str(tmp_path), token_data, user_name, subscriptions)

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


def test_write_azure_cli_files_adds_native_azure_cli_cache_entry(tmp_path):
    user_name = "alden@example.com"
    claims = {
        "aud": "https://management.azure.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "oid": "00000000-0000-0000-0000-000000000001",
        "preferred_username": user_name,
        "tid": microsoft_admin_commands.TENANT_ID,
    }
    token_data = {
        "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "token_type": "Bearer",
        "access_token": _jwt(claims),
        "refresh_token": "refresh-token",
        "scope": microsoft_admin_commands.microsoft_admin_arm_device_scope_string(),
        "expires_in": 3600,
        "expires_on": int(time.time()) + 3600,
        "username": user_name,
    }

    microsoft_admin_commands._write_azure_cli_files(str(tmp_path), token_data, user_name, [])

    cache_data = json.loads((tmp_path / "msal_token_cache.json").read_text(encoding="utf-8"))
    access_tokens = list((cache_data.get("AccessToken") or {}).values())
    assert any(
        item.get("client_id") == microsoft_admin_commands.AZURE_CLI_CLIENT_ID
        and item.get("target") == microsoft_admin_commands.AZURE_CLI_ARM_TARGET
        for item in access_tokens
    )


def test_write_azure_cli_files_synthesizes_account_metadata_from_access_token(tmp_path):
    user_name = "alden@example.com"
    oid = "00000000-0000-0000-0000-000000000001"
    claims = {
        "aud": "https://management.azure.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "oid": oid,
        "preferred_username": user_name,
        "tid": microsoft_admin_commands.TENANT_ID,
    }
    token_data = {
        "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "token_type": "Bearer",
        "access_token": _jwt(claims),
        "refresh_token": "refresh-token",
        "scope": microsoft_admin_commands.microsoft_admin_arm_device_scope_string(),
        "expires_in": 3600,
        "expires_on": int(time.time()) + 3600,
        "username": user_name,
    }
    subscriptions = [
        {
            "subscriptionId": "11111111-1111-1111-1111-111111111111",
            "displayName": "Production",
            "state": "Enabled",
            "tenantId": microsoft_admin_commands.TENANT_ID,
        }
    ]

    microsoft_admin_commands._write_azure_cli_files(str(tmp_path), token_data, user_name, subscriptions)

    cache = msal.SerializableTokenCache()
    cache.deserialize((tmp_path / "msal_token_cache.json").read_text(encoding="utf-8"))
    accounts = list(
        cache.search(
            cache.CredentialType.ACCOUNT,
            query={"environment": "login.microsoftonline.com"},
        )
    )
    assert len(accounts) == 1
    assert accounts[0]["home_account_id"] == f"{oid}.{microsoft_admin_commands.TENANT_ID}"
    assert accounts[0]["username"] == user_name


def test_microsoft_admin_diagnostics_ignores_optional_profile_gaps():
    status, message = diagnostics._microsoft_admin_diagnostic_summary({
        "graph": {"status": "available", "label": "Microsoft Graph Admin"},
        "arm": {"status": "available", "label": "Azure Resource Manager"},
        "exchange": {"status": "available", "label": "Exchange Online"},
        "teams": {"status": "missing", "label": "Teams Admin"},
        "sharepoint": {"status": "not_checked", "label": "SharePoint / PnP"},
    })

    assert status == "success"
    assert "Teams Admin" not in message
    assert "SharePoint" not in message


def test_microsoft_admin_diagnostics_ignores_optional_missing_consent():
    status, message = diagnostics._microsoft_admin_diagnostic_summary({
        "graph": {"status": "available", "label": "Microsoft Graph Admin"},
        "arm": {"status": "available", "label": "Azure Resource Manager"},
        "exchange": {"status": "available", "label": "Exchange Online"},
        "teams": {"status": "missing_consent", "label": "Teams Admin"},
        "sharepoint": {"status": "not_checked", "label": "SharePoint / PnP"},
    })

    assert status == "success"
    assert "Teams Admin" not in message
    assert "SharePoint" not in message


def test_microsoft_admin_default_graph_scopes_include_teams_prerequisites():
    expected = {
        "https://graph.microsoft.com/User.Read.All",
        "https://graph.microsoft.com/AppCatalog.ReadWrite.All",
        "https://graph.microsoft.com/TeamSettings.ReadWrite.All",
        "https://graph.microsoft.com/Channel.Delete.All",
        "https://graph.microsoft.com/ChannelSettings.ReadWrite.All",
        "https://graph.microsoft.com/ChannelMember.ReadWrite.All",
    }

    assert expected.issubset(set(microsoft_admin_commands.MICROSOFT_GRAPH_SCOPES))


@pytest.mark.asyncio
async def test_ensure_azure_cli_profile_rejects_tokens_without_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_CLI_USER_CONFIG_ROOT", str(tmp_path))
    result = await microsoft_admin_commands.ensure_azure_cli_profile(
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

    monkeypatch.setattr(azure_cli, "run_command", fake_run_command)

    result = await microsoft_admin_commands.validate_azure_cli_profile(uuid.uuid4())

    assert result["ready"] is True
    assert "account get-access-token" in str(called["command"])
    assert "account show" not in str(called["command"])
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_AZURE_CLI_ALLOWED_BINARIES


@pytest.mark.asyncio
async def test_ms_azure_cli_uses_native_azure_cli_execution_path(monkeypatch):
    called: dict[str, object] = {}

    class Result:
        success = True
        stdout = "[]"

        def to_dict(self):
            return {
                "stdout": self.stdout,
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": len(self.stdout),
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_token(_user_id, _scope, **_kwargs):
        called["require_account_metadata"] = _kwargs.get("require_account_metadata")
        return {
            "access_token": "access-token",
            "expires_on": int(time.time()) + 3600,
            "username": "alden@example.com",
        }

    async def fake_profile(user_id, token_data, subscriptions_result=None):
        called["profile_user_id"] = user_id
        called["profile_token"] = token_data["access_token"]
        return {"ready": True}

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["command"] = command
        called["timeout"] = timeout
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(azure_cli, "_get_fresh_microsoft_admin_token_for_scope", fake_token)
    monkeypatch.setattr(azure_cli, "ensure_azure_cli_profile", fake_profile)
    monkeypatch.setattr(azure_cli, "run_command", fake_run_command)
    user_id = uuid.uuid4()

    result = await microsoft_admin_commands.run_ms_azure_cli_tool(
        {"command": "account show", "timeout": 30},
        user_id,
    )

    assert called["command"] == "az account show"
    assert called["profile_user_id"] == user_id
    assert called["timeout"] == 30
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_AZURE_CLI_ALLOWED_BINARIES
    assert called["require_account_metadata"] is True
    assert result["status"] == "success"
    assert result["connector"] == "ms_azure_cli"
    assert result["mode"] == "ms_azure_cli"


@pytest.mark.asyncio
async def test_ms_azure_cli_costmanagement_query_uses_native_cli_path(monkeypatch):
    called = {}

    async def fake_token(_user_id, _scope, **kwargs):
        called["require_account_metadata"] = kwargs.get("require_account_metadata")
        return {
            "access_token": "access-token",
            "expires_on": int(time.time()) + 3600,
            "username": "alden@example.com",
        }

    async def fake_profile(user_id, token_data):
        called["profile_user_id"] = user_id
        called["profile_token"] = token_data
        return {"ready": True, "config_dir": "/tmp/ms-admin-cli"}

    async def fake_run_command(command, timeout, env=None, allowed_binaries=None, **_kwargs):
        called["command"] = command
        called["timeout"] = timeout
        called["env"] = env or {}
        called["allowed_binaries"] = allowed_binaries

        class Result:
            success = True

            def to_dict(self):
                return {
                    "stdout": "{}",
                    "stderr": "",
                    "exit_code": 0,
                    "timed_out": False,
                    "output_truncated": False,
                    "stdout_chars": 2,
                    "stderr_chars": 0,
                    "error": None,
                }

        return Result()

    monkeypatch.setattr(azure_cli, "_get_fresh_microsoft_admin_token_for_scope", fake_token)
    monkeypatch.setattr(azure_cli, "ensure_azure_cli_profile", fake_profile)
    monkeypatch.setattr(azure_cli, "run_command", fake_run_command)
    user_id = uuid.uuid4()

    result = await microsoft_admin_commands.run_ms_azure_cli_tool(
        {"command": "costmanagement query --type Usage", "timeout": 30},
        user_id,
    )

    assert called["command"] == "az costmanagement query --type Usage"
    assert called["profile_user_id"] == user_id
    assert called["timeout"] == 30
    assert called["env"]["AZURE_CONFIG_DIR"] == "/tmp/ms-admin-cli"
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_AZURE_CLI_ALLOWED_BINARIES
    assert called["require_account_metadata"] is True
    assert result["status"] == "success"
    assert result["connector"] == "ms_azure_cli"


@pytest.mark.asyncio
async def test_ms_azure_cli_failure_surfaces_stderr_message(monkeypatch):
    class Result:
        success = False

        def to_dict(self):
            return {
                "stdout": "",
                "stderr": "ERROR: 'query' is misspelled or not recognized by the system.\n",
                "exit_code": 2,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": 0,
                "stderr_chars": 61,
                "error": None,
            }

    async def fake_token(_user_id, _scope, **_kwargs):
        return {
            "access_token": "access-token",
            "expires_on": int(time.time()) + 3600,
            "username": "alden@example.com",
        }

    async def fake_profile(user_id, token_data, subscriptions_result=None):
        return {"ready": True}

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        return Result()

    monkeypatch.setattr(azure_cli, "_get_fresh_microsoft_admin_token_for_scope", fake_token)
    monkeypatch.setattr(azure_cli, "ensure_azure_cli_profile", fake_profile)
    monkeypatch.setattr(azure_cli, "run_command", fake_run_command)

    result = await microsoft_admin_commands.run_ms_azure_cli_tool(
        {"command": "account show"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "command_failed"
    assert result["message"] == "ERROR: 'query' is misspelled or not recognized by the system."


@pytest.mark.asyncio
async def test_ms_graph_powershell_rejects_github_commands(monkeypatch):
    async def unexpected_token_lookup(*_args, **_kwargs):
        raise AssertionError("token lookup should not run for rejected GitHub command")

    monkeypatch.setattr(powershell_graph, "get_microsoft_admin_token", unexpected_token_lookup)

    result = await microsoft_admin_commands.run_ms_graph_powershell_tool(
        {"script": "gh run list --repo owner/repo"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["connector"] == "ms_graph_powershell"
    assert result["error_type"] == "unsupported_command"
    assert "GitHub connector" in result["error"]


@pytest.mark.asyncio
async def test_ms_graph_powershell_uses_only_graph_token(monkeypatch):
    profiles: list[tuple[str, dict]] = []
    called: dict[str, object] = {}

    async def fake_get_token(_user_id, profile, **context):
        profiles.append((profile, context))
        return {
            "access_token": f"{profile}-access-token",
            "username": "alden@example.com",
            "expires_on": int(time.time()) + 3600,
        }

    class Result:
        success = True

        def to_dict(self):
            return {
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": 2,
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["command"] = command
        called["timeout"] = timeout
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(powershell_graph, "get_microsoft_admin_token", fake_get_token)
    monkeypatch.setattr(powershell_common, "run_command", fake_run_command)

    result = await microsoft_admin_commands.run_ms_graph_powershell_tool(
        {"script": "Connect-AIPlatformGraph\nGet-MgUser -Top 1", "timeout": 45},
        uuid.uuid4(),
    )

    assert profiles == [("graph", {})]
    assert called["timeout"] == 45
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_POWERSHELL_ALLOWED_BINARIES
    assert str(called["command"]).startswith("pwsh -NoLogo -NoProfile -NonInteractive -Command ")
    env = called["env"]
    assert env["AI_PLATFORM_GRAPH_ACCESS_TOKEN"] == "graph-access-token"
    assert env["AI_PLATFORM_MS_USERNAME"] == "alden@example.com"
    assert "AI_PLATFORM_ARM_ACCESS_TOKEN" not in env
    assert "AI_PLATFORM_EXCHANGE_ACCESS_TOKEN" not in env
    assert "AI_PLATFORM_TEAMS_ACCESS_TOKEN" not in env
    assert "AI_PLATFORM_PNP_ACCESS_TOKEN" not in env
    assert result["status"] == "success"
    assert result["connector"] == "ms_graph_powershell"


@pytest.mark.asyncio
async def test_ms_teams_powershell_requires_graph_and_teams_tokens(monkeypatch):
    profiles: list[str] = []
    called: dict[str, object] = {}

    async def fake_get_token(_user_id, profile, **_context):
        profiles.append(profile)
        return {
            "access_token": f"{profile}-access-token",
            "username": "alden@example.com",
            "expires_on": int(time.time()) + 3600,
        }

    class Result:
        success = True

        def to_dict(self):
            return {
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": 2,
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["command"] = command
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(powershell_teams, "get_microsoft_admin_token", fake_get_token)
    monkeypatch.setattr(powershell_common, "run_command", fake_run_command)

    result = await microsoft_admin_commands.run_ms_teams_powershell_tool(
        {"script": "Connect-AIPlatformTeams\nGet-Team"},
        uuid.uuid4(),
    )

    assert profiles == ["graph", "teams"]
    env = called["env"]
    assert env["AI_PLATFORM_GRAPH_ACCESS_TOKEN"] == "graph-access-token"
    assert env["AI_PLATFORM_TEAMS_ACCESS_TOKEN"] == "teams-access-token"
    assert "AI_PLATFORM_ARM_ACCESS_TOKEN" not in env
    assert "AI_PLATFORM_EXCHANGE_ACCESS_TOKEN" not in env
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_POWERSHELL_ALLOWED_BINARIES
    assert result["status"] == "success"
    assert result["connector"] == "ms_teams_powershell"


@pytest.mark.asyncio
async def test_ms_sharepoint_pnp_requires_site_url_before_token_lookup(monkeypatch):
    async def unexpected_token_lookup(*_args, **_kwargs):
        raise AssertionError("token lookup should not run without a SharePoint URL")

    monkeypatch.setattr(powershell_pnp, "get_microsoft_admin_token", unexpected_token_lookup)

    result = await microsoft_admin_commands.run_ms_sharepoint_pnp_powershell_tool(
        {"script": "Connect-AIPlatformPnP\nGet-PnPList"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["connector"] == "ms_sharepoint_pnp_powershell"
    assert result["error_type"] == "missing_site_url"


@pytest.mark.asyncio
async def test_ms_sharepoint_pnp_uses_target_sharepoint_token(monkeypatch):
    profiles: list[tuple[str, dict]] = []
    called: dict[str, object] = {}
    site_url = "https://tenant.sharepoint.com/sites/example"

    async def fake_get_token(_user_id, profile, **context):
        profiles.append((profile, context))
        return {
            "access_token": "sharepoint-access-token",
            "username": "alden@example.com",
            "expires_on": int(time.time()) + 3600,
        }

    class Result:
        success = True

        def to_dict(self):
            return {
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": 2,
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(powershell_pnp, "get_microsoft_admin_token", fake_get_token)
    monkeypatch.setattr(powershell_common, "run_command", fake_run_command)

    result = await microsoft_admin_commands.run_ms_sharepoint_pnp_powershell_tool(
        {"site_url": site_url, "script": "Connect-AIPlatformPnP\nGet-PnPList"},
        uuid.uuid4(),
    )

    assert profiles == [("sharepoint", {"site_url": site_url})]
    env = called["env"]
    assert env["AI_PLATFORM_PNP_ACCESS_TOKEN"] == "sharepoint-access-token"
    assert env["AI_PLATFORM_PNP_URL"] == site_url
    assert "AI_PLATFORM_GRAPH_ACCESS_TOKEN" not in env
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_POWERSHELL_ALLOWED_BINARIES
    assert result["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner_name", "profile", "token_env_name", "script"),
    [
        ("run_ms_exchange_powershell_tool", "exchange", "AI_PLATFORM_EXCHANGE_ACCESS_TOKEN", "Connect-AIPlatformExchange\nGet-Mailbox -ResultSize 1"),
        ("run_ms_az_powershell_tool", "arm", "AI_PLATFORM_ARM_ACCESS_TOKEN", "Connect-AIPlatformAz\nGet-AzResourceGroup"),
    ],
)
async def test_module_specific_powershell_tools_inject_only_required_token(
    monkeypatch,
    runner_name,
    profile,
    token_env_name,
    script,
):
    profiles: list[str] = []
    called: dict[str, object] = {}

    async def fake_get_token(_user_id, received_profile, **_context):
        profiles.append(received_profile)
        return {
            "access_token": f"{received_profile}-access-token",
            "username": "alden@example.com",
            "expires_on": int(time.time()) + 3600,
        }

    class Result:
        success = True

        def to_dict(self):
            return {
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": 2,
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    module = powershell_exchange if runner_name == "run_ms_exchange_powershell_tool" else powershell_az
    monkeypatch.setattr(module, "get_microsoft_admin_token", fake_get_token)
    monkeypatch.setattr(powershell_common, "run_command", fake_run_command)

    runner = getattr(microsoft_admin_commands, runner_name)
    result = await runner({"script": script}, uuid.uuid4())

    assert profiles == [profile]
    env = called["env"]
    assert env[token_env_name] == f"{profile}-access-token"
    for other_token_name in {
        "AI_PLATFORM_GRAPH_ACCESS_TOKEN",
        "AI_PLATFORM_EXCHANGE_ACCESS_TOKEN",
        "AI_PLATFORM_TEAMS_ACCESS_TOKEN",
        "AI_PLATFORM_PNP_ACCESS_TOKEN",
        "AI_PLATFORM_ARM_ACCESS_TOKEN",
    } - {token_env_name}:
        assert other_token_name not in env
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_POWERSHELL_ALLOWED_BINARIES
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_ms_bicep_uses_only_bicep_binary(monkeypatch):
    called: dict[str, object] = {}

    class Result:
        success = True

        def to_dict(self):
            return {
                "stdout": "Bicep CLI version 0.0.0",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": 23,
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        called["command"] = command
        called["env"] = env
        called["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(bicep, "run_command", fake_run_command)

    result = await microsoft_admin_commands.run_ms_bicep_tool(
        {"command": "version"},
        uuid.uuid4(),
    )

    assert called["command"] == "bicep version"
    assert called["allowed_binaries"] == microsoft_admin_commands.MS_BICEP_ALLOWED_BINARIES
    assert result["status"] == "success"
    assert result["connector"] == "ms_bicep"


@pytest.mark.asyncio
async def test_scoped_token_refresh_uses_current_admin_client_and_preserves_primary_refresh(monkeypatch):
    user_id = uuid.uuid4()
    stored_token = {
        "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "access_token": "primary-access",
        "refresh_token": "primary-refresh",
        "expires_on": int(time.time()) + 3600,
        "delegated_tokens": {
            "graph": {
                "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
                "access_token": "old-graph-access",
                "refresh_token": "graph-refresh",
                "expires_on": int(time.time()) - 10,
            }
        },
    }
    captured: dict[str, object] = {}

    async def fake_retrieve(provider, received_user_id):
        assert provider == "microsoft_admin"
        assert received_user_id == user_id
        return stored_token

    async def fake_store(provider, received_user_id, token_data):
        assert provider == "microsoft_admin"
        assert received_user_id == user_id
        captured["stored"] = token_data
        return True

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "token_type": "Bearer",
                "access_token": "new-graph-access",
                "refresh_token": "new-graph-refresh",
                "scope": " ".join(microsoft_admin_commands.MICROSOFT_GRAPH_SCOPES),
                "expires_in": 3600,
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse()

    monkeypatch.setattr(tokens, "retrieve_token", fake_retrieve)
    monkeypatch.setattr(tokens, "store_token", fake_store)
    monkeypatch.setattr(tokens.httpx, "AsyncClient", FakeClient)

    result = await microsoft_admin_commands._get_fresh_microsoft_admin_token_for_scope(user_id, microsoft_admin_commands.MICROSOFT_GRAPH_SCOPE)

    assert result["access_token"] == "new-graph-access"
    assert captured["data"]["client_id"] == microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID
    assert captured["data"]["refresh_token"] == "graph-refresh"
    assert "https://graph.microsoft.com/User.ReadWrite.All" in captured["data"]["scope"]
    stored = captured["stored"]
    assert stored["refresh_token"] == "primary-refresh"
    assert stored["delegated_tokens"]["graph"]["refresh_token"] == "new-graph-refresh"


@pytest.mark.asyncio
async def test_scoped_arm_token_without_cli_account_metadata_refreshes_when_required(monkeypatch):
    user_id = uuid.uuid4()
    stored_token = {
        "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "access_token": "primary-graph-access",
        "refresh_token": "primary-refresh",
        "expires_on": int(time.time()) + 3600,
        "scope_profile": "graph",
        "username": "alden@example.com",
        "delegated_tokens": {
            "arm": {
                "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
                "access_token": "old-arm-access",
                "scope": microsoft_admin_commands.AZURE_ARM_SCOPE,
                "scope_profile": "arm",
                "expires_on": int(time.time()) + 3600,
            }
        },
    }
    captured: dict[str, object] = {}
    client_info = _base64url_json({"uid": "uid-value", "utid": microsoft_admin_commands.TENANT_ID})

    async def fake_retrieve(provider, received_user_id):
        assert provider == "microsoft_admin"
        assert received_user_id == user_id
        return stored_token

    async def fake_store(provider, received_user_id, token_data):
        assert provider == "microsoft_admin"
        assert received_user_id == user_id
        captured["stored"] = token_data
        return True

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "token_type": "Bearer",
                "access_token": "new-arm-access",
                "refresh_token": "new-arm-refresh",
                "scope": microsoft_admin_commands.AZURE_ARM_SCOPE,
                "client_info": client_info,
                "expires_in": 3600,
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse()

    monkeypatch.setattr(tokens, "retrieve_token", fake_retrieve)
    monkeypatch.setattr(tokens, "store_token", fake_store)
    monkeypatch.setattr(tokens.httpx, "AsyncClient", FakeClient)

    result = await microsoft_admin_commands._get_fresh_microsoft_admin_token_for_scope(
        user_id,
        microsoft_admin_commands.AZURE_ARM_SCOPE,
        require_account_metadata=True,
    )

    assert result["access_token"] == "new-arm-access"
    assert result["client_info"] == client_info
    assert captured["data"]["refresh_token"] == "primary-refresh"
    stored = captured["stored"]
    assert stored["delegated_tokens"]["arm"]["access_token"] == "new-arm-access"
    assert stored["delegated_tokens"]["arm"]["client_info"] == client_info


@pytest.mark.asyncio
async def test_scoped_token_consent_required_does_not_return_primary_access_token(monkeypatch):
    user_id = uuid.uuid4()
    stored_token = {
        "client_id": microsoft_admin_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "access_token": "primary-graph-access",
        "refresh_token": "primary-refresh",
        "expires_on": int(time.time()) + 3600,
        "scope_profile": "graph",
        "username": "alden@example.com",
    }

    async def fake_retrieve(provider, received_user_id):
        assert provider == "microsoft_admin"
        assert received_user_id == user_id
        return stored_token

    class FakeResponse:
        status_code = 400
        text = '{"error":"invalid_grant"}'

        def json(self):
            return {
                "error": "invalid_grant",
                "error_description": "AADSTS65001: The user or administrator has not consented to use this application for this resource.",
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            return FakeResponse()

    monkeypatch.setattr(tokens, "retrieve_token", fake_retrieve)
    monkeypatch.setattr(tokens.httpx, "AsyncClient", FakeClient)

    result = await microsoft_admin_commands._get_fresh_microsoft_admin_token_for_scope(user_id, microsoft_admin_commands.AZURE_ARM_SCOPE)

    assert result["error_type"] == "consent_required"
    assert "access_token" not in result
    assert result["scope_profile"] == "arm"
    assert "Tenant admin consent is required for Azure Resource Manager" in result["refresh_error"]


@pytest.mark.asyncio
async def test_primary_token_from_retired_app_requires_reconnect(monkeypatch):
    user_id = uuid.uuid4()

    async def fake_retrieve(provider, received_user_id):
        assert provider == "microsoft_admin"
        assert received_user_id == user_id
        return {
            "client_id": "04b07795-8ddb-461a-bbee-02f9e1bf7b46",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_on": int(time.time()) + 3600,
            "username": "alden@example.com",
        }

    monkeypatch.setattr(tokens, "retrieve_token", fake_retrieve)

    result = await microsoft_admin_commands._get_fresh_microsoft_admin_token(user_id)

    assert result["error_type"] == "reconnect_required"
    assert "access_token" not in result
    assert "retired application" in result["refresh_error"]


class _FakeGraphResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _fake_graph_client(monkeypatch, responses: list[_FakeGraphResponse], calls: list[dict]):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, headers=None, json=None):
            calls.append({"method": method, "url": url, "headers": headers, "json": json})
            if not responses:
                raise AssertionError("No fake Graph response queued")
            return responses.pop(0)

    monkeypatch.setattr(azure_cli.httpx, "AsyncClient", FakeClient)


async def _fake_graph_token(_user_id, _scope):
    return {"access_token": "graph-token", "expires_on": int(time.time()) + 3600}


@pytest.mark.asyncio
async def test_ms_graph_auto_follows_next_link(monkeypatch):
    calls: list[dict] = []
    responses = [
        _FakeGraphResponse(
            200,
            {
                "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users",
                "value": [{"id": "1"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=abc",
            },
        ),
        _FakeGraphResponse(200, {"value": [{"id": "2"}]}),
    ]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(graph, "_get_fresh_microsoft_admin_token_for_scope", _fake_graph_token)

    result = await microsoft_admin_commands.run_ms_graph_tool(
        {"path": "/users?$top=1&$select=id"},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert result["connector"] == "ms_graph"
    assert result["result"]["value"] == [{"id": "1"}, {"id": "2"}]
    assert result["result"]["pagination"]["auto_paged"] is True
    assert result["result"]["pagination"]["pages_fetched"] == 2
    assert calls[1]["url"] == "https://graph.microsoft.com/v1.0/users?$skiptoken=abc"


@pytest.mark.asyncio
async def test_ms_graph_users_skip_is_applied_locally(monkeypatch):
    calls: list[dict] = []
    responses = [
        _FakeGraphResponse(
            200,
            {
                "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users",
                "value": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            },
        ),
    ]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(graph, "_get_fresh_microsoft_admin_token_for_scope", _fake_graph_token)

    result = await microsoft_admin_commands.run_ms_graph_tool(
        {"path": "/users?$top=999&$skip=1&$select=id"},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert "%24skip" not in calls[0]["url"]
    assert "$skip" not in calls[0]["url"]
    assert result["result"]["value"] == [{"id": "2"}, {"id": "3"}]
    assert result["result"]["pagination"]["local_skip_applied"] == 1
    assert "does not support manual $skip" in result["result"]["warning"]


@pytest.mark.asyncio
async def test_ms_graph_skip_is_not_rewritten_for_user_child_collections(monkeypatch):
    calls: list[dict] = []
    responses = [_FakeGraphResponse(200, {"value": [{"id": "message-2"}]})]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(graph, "_get_fresh_microsoft_admin_token_for_scope", _fake_graph_token)

    result = await microsoft_admin_commands.run_ms_graph_tool(
        {"path": "/users/user-1/messages?$top=1&$skip=1"},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert "$skip=1" in calls[0]["url"]
    assert "warning" not in result["result"]


@pytest.mark.asyncio
async def test_ms_graph_users_local_skip_fetches_past_skipped_items(monkeypatch):
    calls: list[dict] = []
    responses = [
        _FakeGraphResponse(
            200,
            {
                "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users",
                "value": [{"id": "1"}, {"id": "2"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=abc",
            },
        ),
        _FakeGraphResponse(200, {"value": [{"id": "3"}, {"id": "4"}]}),
    ]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(graph, "_get_fresh_microsoft_admin_token_for_scope", _fake_graph_token)

    result = await microsoft_admin_commands.run_ms_graph_tool(
        {"path": "/users?$skip=2&$select=id", "max_items": 2},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert len(calls) == 2
    assert result["result"]["value"] == [{"id": "3"}, {"id": "4"}]
    assert result["result"]["pagination"]["pre_skip_count"] == 4
    assert result["result"]["pagination"]["returned_count"] == 2


@pytest.mark.asyncio
async def test_ms_graph_surfaces_graph_error_message(monkeypatch):
    calls: list[dict] = []
    responses = [
        _FakeGraphResponse(
            400,
            {"error": {"code": "Request_BadRequest", "message": "'$skip' is not supported by the service."}},
        ),
    ]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(graph, "_get_fresh_microsoft_admin_token_for_scope", _fake_graph_token)

    result = await microsoft_admin_commands.run_ms_graph_tool(
        {"path": "/groups?$skip=5"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "Request_BadRequest"
    assert result["message"] == "'$skip' is not supported by the service."
