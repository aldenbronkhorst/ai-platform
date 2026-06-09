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
    assert called["allowed_binaries"] == azure_commands.MS_ADMIN_ALLOWED_BINARIES


@pytest.mark.asyncio
async def test_ms_admin_azure_cli_mode_uses_single_ms_admin_execution_path(monkeypatch):
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

    async def fake_token(_user_id):
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

    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token", fake_token)
    monkeypatch.setattr(azure_commands, "ensure_azure_cli_profile", fake_profile)
    monkeypatch.setattr(azure_commands, "run_command", fake_run_command)
    user_id = uuid.uuid4()

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "azure_cli", "command": "account show", "timeout": 30},
        user_id,
    )

    assert called["command"] == "az account show"
    assert called["profile_user_id"] == user_id
    assert called["timeout"] == 30
    assert called["allowed_binaries"] == azure_commands.MS_ADMIN_ALLOWED_BINARIES
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

    monkeypatch.setattr(azure_commands.httpx, "AsyncClient", FakeClient)


async def _fake_graph_token(_user_id, _scope):
    return {"access_token": "graph-token", "expires_on": int(time.time()) + 3600}


@pytest.mark.asyncio
async def test_ms_admin_graph_request_auto_follows_next_link(monkeypatch):
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "graph_request", "path": "/users?$top=1&$select=id"},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert result["result"]["value"] == [{"id": "1"}, {"id": "2"}]
    assert result["result"]["pagination"]["auto_paged"] is True
    assert result["result"]["pagination"]["pages_fetched"] == 2
    assert calls[1]["url"] == "https://graph.microsoft.com/v1.0/users?$skiptoken=abc"


@pytest.mark.asyncio
async def test_ms_admin_graph_users_skip_is_applied_locally(monkeypatch):
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "graph_request", "path": "/users?$top=999&$skip=1&$select=id"},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert "%24skip" not in calls[0]["url"]
    assert "$skip" not in calls[0]["url"]
    assert result["result"]["value"] == [{"id": "2"}, {"id": "3"}]
    assert result["result"]["pagination"]["local_skip_applied"] == 1
    assert "does not support manual $skip" in result["result"]["warning"]


@pytest.mark.asyncio
async def test_ms_admin_graph_skip_is_not_rewritten_for_user_child_collections(monkeypatch):
    calls: list[dict] = []
    responses = [_FakeGraphResponse(200, {"value": [{"id": "message-2"}]})]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "graph_request", "path": "/users/user-1/messages?$top=1&$skip=1"},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert "$skip=1" in calls[0]["url"]
    assert "warning" not in result["result"]


@pytest.mark.asyncio
async def test_ms_admin_graph_users_local_skip_fetches_past_skipped_items(monkeypatch):
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "graph_request", "path": "/users?$skip=2&$select=id", "max_items": 2},
        uuid.uuid4(),
    )

    assert result["status"] == "success"
    assert len(calls) == 2
    assert result["result"]["value"] == [{"id": "3"}, {"id": "4"}]
    assert result["result"]["pagination"]["pre_skip_count"] == 4
    assert result["result"]["pagination"]["returned_count"] == 2


@pytest.mark.asyncio
async def test_ms_admin_graph_request_surfaces_graph_error_message(monkeypatch):
    calls: list[dict] = []
    responses = [
        _FakeGraphResponse(
            400,
            {"error": {"code": "Request_BadRequest", "message": "'$skip' is not supported by the service."}},
        ),
    ]
    _fake_graph_client(monkeypatch, responses, calls)
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_admin_tool(
        {"mode": "graph_request", "path": "/groups?$skip=5"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "Request_BadRequest"
    assert result["message"] == "'$skip' is not supported by the service."
