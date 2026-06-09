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
    assert azure_commands.microsoft_admin_scope_profile("unknown") == "graph"

    arm_scope = azure_commands.microsoft_admin_device_scope_string("arm").split()
    graph_scope = azure_commands.microsoft_admin_device_scope_string("graph").split()
    exchange_scope = azure_commands.microsoft_admin_device_scope_string("exchange").split()

    assert azure_commands.AZURE_ARM_SCOPE in arm_scope
    assert "https://graph.microsoft.com/User.ReadWrite.All" in graph_scope
    assert "https://graph.microsoft.com/Directory.ReadWrite.All" in graph_scope
    assert azure_commands.EXCHANGE_ONLINE_SCOPE in exchange_scope
    assert not set(azure_commands.MICROSOFT_GRAPH_SCOPES).intersection(arm_scope)
    assert azure_commands.EXCHANGE_ONLINE_SCOPE not in graph_scope
    assert "offline_access" in graph_scope


def test_microsoft_admin_client_id_is_profile_specific(monkeypatch):
    monkeypatch.setattr(azure_commands, "MICROSOFT_ADMIN_CLIENT_ID", "admin-client-id")

    assert azure_commands.microsoft_admin_client_id_for_scope_profile("arm") == "admin-client-id"
    assert azure_commands.microsoft_admin_client_id_for_scope_profile("graph") == "admin-client-id"
    assert azure_commands.microsoft_admin_client_id_for_scope_profile("exchange") == "admin-client-id"
    assert azure_commands.microsoft_admin_app_name_for_scope_profile("arm") == azure_commands.MICROSOFT_ADMIN_APP_DISPLAY_NAME
    assert azure_commands.microsoft_admin_app_name_for_scope_profile("graph") == azure_commands.MICROSOFT_ADMIN_APP_DISPLAY_NAME


def test_extract_azure_username_does_not_use_old_fake_fallback():
    assert azure_commands.extract_azure_username({"username": "azure-user"}) == ""

    claims = {
        "aud": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
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
        "aud": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "oid": "00000000-0000-0000-0000-000000000001",
        "preferred_username": user_name,
        "sub": "subject-id",
        "tid": azure_commands.TENANT_ID,
    }
    token_data = {
        "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
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


def test_write_azure_cli_files_synthesizes_account_metadata_from_access_token(tmp_path):
    user_name = "alden@example.com"
    oid = "00000000-0000-0000-0000-000000000001"
    claims = {
        "aud": "https://management.azure.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "oid": oid,
        "preferred_username": user_name,
        "tid": azure_commands.TENANT_ID,
    }
    token_data = {
        "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "token_type": "Bearer",
        "access_token": _jwt(claims),
        "refresh_token": "refresh-token",
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

    cache = msal.SerializableTokenCache()
    cache.deserialize((tmp_path / "msal_token_cache.json").read_text(encoding="utf-8"))
    accounts = list(
        cache.search(
            cache.CredentialType.ACCOUNT,
            query={"environment": "login.microsoftonline.com"},
        )
    )
    assert len(accounts) == 1
    assert accounts[0]["home_account_id"] == f"{oid}.{azure_commands.TENANT_ID}"
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
    assert called["allowed_binaries"] == azure_commands.MS_AZURE_CLI_ALLOWED_BINARIES


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

    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", fake_token)
    monkeypatch.setattr(azure_commands, "ensure_azure_cli_profile", fake_profile)
    monkeypatch.setattr(azure_commands, "run_command", fake_run_command)
    user_id = uuid.uuid4()

    result = await azure_commands.run_ms_azure_cli_tool(
        {"command": "account show", "timeout": 30},
        user_id,
    )

    assert called["command"] == "az account show"
    assert called["profile_user_id"] == user_id
    assert called["timeout"] == 30
    assert called["allowed_binaries"] == azure_commands.MS_AZURE_CLI_ALLOWED_BINARIES
    assert called["require_account_metadata"] is True
    assert result["status"] == "success"
    assert result["connector"] == "ms_azure_cli"
    assert result["mode"] == "azure_cli"


@pytest.mark.asyncio
async def test_ms_azure_cli_costmanagement_query_cli_returns_rest_guidance(monkeypatch):
    async def unexpected_token_lookup(*_args, **_kwargs):
        raise AssertionError("token lookup should not run for unsupported costmanagement query")

    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token", unexpected_token_lookup)

    result = await azure_commands.run_ms_azure_cli_tool(
        {"command": "costmanagement query --type Usage"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["connector"] == "ms_azure_cli"
    assert result["error_type"] == "unsupported_costmanagement_query_cli"
    assert "az rest" in result["message"]
    assert "Microsoft.CostManagement/query" in result["message"]


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

    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", fake_token)
    monkeypatch.setattr(azure_commands, "ensure_azure_cli_profile", fake_profile)
    monkeypatch.setattr(azure_commands, "run_command", fake_run_command)

    result = await azure_commands.run_ms_azure_cli_tool(
        {"command": "account show"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "command_failed"
    assert result["message"] == "ERROR: 'query' is misspelled or not recognized by the system."


@pytest.mark.asyncio
async def test_ms_powershell_rejects_github_commands(monkeypatch):
    async def unexpected_token_lookup(*_args, **_kwargs):
        raise AssertionError("token lookup should not run for rejected GitHub command")

    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token", unexpected_token_lookup)

    result = await azure_commands.run_ms_powershell_tool(
        {"script": "gh run list --repo owner/repo"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["connector"] == "ms_powershell"
    assert result["error_type"] == "unsupported_command"
    assert "GitHub connector" in result["error"]


@pytest.mark.asyncio
async def test_scoped_token_refresh_uses_current_admin_client_and_preserves_primary_refresh(monkeypatch):
    user_id = uuid.uuid4()
    stored_token = {
        "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "access_token": "primary-access",
        "refresh_token": "primary-refresh",
        "expires_on": int(time.time()) + 3600,
        "delegated_tokens": {
            "graph": {
                "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
                "access_token": "old-graph-access",
                "refresh_token": "graph-refresh",
                "expires_on": int(time.time()) - 10,
            }
        },
    }
    captured: dict[str, object] = {}

    async def fake_retrieve(provider, received_user_id):
        assert provider == "azure"
        assert received_user_id == user_id
        return stored_token

    async def fake_store(provider, received_user_id, token_data):
        assert provider == "azure"
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
                "scope": " ".join(azure_commands.MICROSOFT_GRAPH_SCOPES),
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

    monkeypatch.setattr(azure_commands, "retrieve_token", fake_retrieve)
    monkeypatch.setattr(azure_commands, "store_token", fake_store)
    monkeypatch.setattr(azure_commands.httpx, "AsyncClient", FakeClient)

    result = await azure_commands._get_fresh_azure_token_for_scope(user_id, azure_commands.MICROSOFT_GRAPH_SCOPE)

    assert result["access_token"] == "new-graph-access"
    assert captured["data"]["client_id"] == azure_commands.MICROSOFT_ADMIN_CLIENT_ID
    assert captured["data"]["refresh_token"] == "graph-refresh"
    assert "https://graph.microsoft.com/User.ReadWrite.All" in captured["data"]["scope"]
    stored = captured["stored"]
    assert stored["refresh_token"] == "primary-refresh"
    assert stored["delegated_tokens"]["graph"]["refresh_token"] == "new-graph-refresh"


@pytest.mark.asyncio
async def test_scoped_arm_token_without_cli_account_metadata_refreshes_when_required(monkeypatch):
    user_id = uuid.uuid4()
    stored_token = {
        "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "access_token": "primary-graph-access",
        "refresh_token": "primary-refresh",
        "expires_on": int(time.time()) + 3600,
        "scope_profile": "graph",
        "username": "alden@example.com",
        "delegated_tokens": {
            "arm": {
                "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
                "access_token": "old-arm-access",
                "scope": azure_commands.AZURE_ARM_SCOPE,
                "scope_profile": "arm",
                "expires_on": int(time.time()) + 3600,
            }
        },
    }
    captured: dict[str, object] = {}
    client_info = _base64url_json({"uid": "uid-value", "utid": azure_commands.TENANT_ID})

    async def fake_retrieve(provider, received_user_id):
        assert provider == "azure"
        assert received_user_id == user_id
        return stored_token

    async def fake_store(provider, received_user_id, token_data):
        assert provider == "azure"
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
                "scope": azure_commands.AZURE_ARM_SCOPE,
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

    monkeypatch.setattr(azure_commands, "retrieve_token", fake_retrieve)
    monkeypatch.setattr(azure_commands, "store_token", fake_store)
    monkeypatch.setattr(azure_commands.httpx, "AsyncClient", FakeClient)

    result = await azure_commands._get_fresh_azure_token_for_scope(
        user_id,
        azure_commands.AZURE_ARM_SCOPE,
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
        "client_id": azure_commands.MICROSOFT_ADMIN_CLIENT_ID,
        "access_token": "primary-graph-access",
        "refresh_token": "primary-refresh",
        "expires_on": int(time.time()) + 3600,
        "scope_profile": "graph",
        "username": "alden@example.com",
    }

    async def fake_retrieve(provider, received_user_id):
        assert provider == "azure"
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

    monkeypatch.setattr(azure_commands, "retrieve_token", fake_retrieve)
    monkeypatch.setattr(azure_commands.httpx, "AsyncClient", FakeClient)

    result = await azure_commands._get_fresh_azure_token_for_scope(user_id, azure_commands.AZURE_ARM_SCOPE)

    assert result["error_type"] == "consent_required"
    assert "access_token" not in result
    assert result["scope_profile"] == "arm"
    assert "Tenant admin consent is required for Azure Resource Manager" in result["refresh_error"]


@pytest.mark.asyncio
async def test_primary_token_from_retired_app_requires_reconnect(monkeypatch):
    user_id = uuid.uuid4()

    async def fake_retrieve(provider, received_user_id):
        assert provider == "azure"
        assert received_user_id == user_id
        return {
            "client_id": "04b07795-8ddb-461a-bbee-02f9e1bf7b46",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_on": int(time.time()) + 3600,
            "username": "alden@example.com",
        }

    monkeypatch.setattr(azure_commands, "retrieve_token", fake_retrieve)

    result = await azure_commands._get_fresh_azure_token(user_id)

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

    monkeypatch.setattr(azure_commands.httpx, "AsyncClient", FakeClient)


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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_graph_tool(
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_graph_tool(
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_graph_tool(
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_graph_tool(
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
    monkeypatch.setattr(azure_commands, "_get_fresh_azure_token_for_scope", _fake_graph_token)

    result = await azure_commands.run_ms_graph_tool(
        {"path": "/groups?$skip=5"},
        uuid.uuid4(),
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "Request_BadRequest"
    assert result["message"] == "'$skip' is not supported by the service."
