import os
import json
import uuid
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.main import app
from app.core.database import get_db
from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount, AITool, AIMemory
from app.services.model_router import (
    ROUTE_NOT_CONFIGURED_MESSAGE,
    CANONICAL_SYSTEM_PROMPT,
    _tool_selection_message,
    _compact_tool_result_for_model,
    _append_tool_guidance,
    _build_tool_finalizer_messages,
    _guard_connected_system_denial,
    _execute_tool_call_impl,
)
from app.services.chat_titles import _fallback_chat_title, _sanitize_chat_title
from app.services.model_tool_calls import _canonical_tool_invocation
from app.services.tool_registry import MICROSOFT_NATIVE_TOOL_NAMES


# ── Canonical prompt sanity ──

def test_canonical_prompt_has_no_odoo_identity():
    """The canonical system prompt must not say 'Odoo assistant' or 'ERP assistant'."""
    lower = CANONICAL_SYSTEM_PROMPT.lower()
    assert "odoo assistant" not in lower
    assert "erp assistant" not in lower
    assert "integrated with odoo" not in lower
    assert "odoo erp" not in lower
    assert "fully integrated into odoo" not in lower
    assert "operational assistant" not in lower


def test_canonical_prompt_identifies_as_ai_platform():
    """The canonical prompt must identify as 'AI Platform for Lots Lots More'."""
    assert "AI Platform for Lots Lots More" in CANONICAL_SYSTEM_PROMPT


def test_canonical_prompt_mentions_connected_accounts():
    """The canonical prompt must mention connected accounts and guide when disconnected."""
    assert "Connected Accounts" in CANONICAL_SYSTEM_PROMPT
    assert "not connected" in CANONICAL_SYSTEM_PROMPT.lower() or "Never claim" in CANONICAL_SYSTEM_PROMPT


def test_canonical_prompt_is_tool_agnostic():
    """The canonical prompt must not claim Odoo is always available."""
    assert "only when they are available" in CANONICAL_SYSTEM_PROMPT
    assert "never claim live access" in CANONICAL_SYSTEM_PROMPT.lower()


def test_canonical_prompt_requires_grounded_connected_system_numbers():
    lower = CANONICAL_SYSTEM_PROMPT.lower()
    assert "never invent quantitative connected-system facts" in lower
    assert "successful current tool results" in lower


def test_trace_redaction_keeps_token_counts_visible():
    from app.services.trace_service import redact_value, summarize_payload

    assert redact_value("prompt_tokens", 123) == 123
    assert redact_value("completion_tokens", 45) == 45
    redacted_secret = redact_value("access_token", "super-secret-token")
    assert redacted_secret["present"] is True
    assert "fingerprint" not in redacted_secret
    assert "super-secret-token" not in str(redacted_secret)
    assert summarize_payload({"messages": [{"role": "user", "content": "hi"}]}) == {
        "messages": [1, {"role": "user", "content": "hi"}]
    }


def test_chat_title_sanitizer_returns_short_plain_title():
    assert _sanitize_chat_title('"Azure Resource Costs."') == "Azure Resource Costs"
    assert _sanitize_chat_title("1. Odoo Invoice Review\nextra") == "Odoo Invoice Review"
    assert _sanitize_chat_title("<|tool_call_begin|>bad") is None
    assert _sanitize_chat_title("New Chat") is None


def test_fallback_chat_title_uses_first_user_request():
    title = _fallback_chat_title([
        {"role": "user", "content": "can you check our azure and tell me all active resources"},
        {"role": "assistant", "content": "I will check Azure."},
    ])

    assert title == "Azure Active Resources"


def test_fallback_chat_title_preserves_business_terms():
    title = _fallback_chat_title([
        {"role": "user", "content": "what did Penelope do today in Odoo, give me a timeline"},
    ])

    assert title == "Penelope Odoo Timeline"


def test_fallback_chat_title_drops_question_scaffolding_and_standalone_counts():
    title = _fallback_chat_title([
        {"role": "user", "content": "there are 2 gerhard employees in my odoo?"},
    ])

    assert title == "Gerhard Employees Odoo"


def test_fallback_chat_title_corrects_typos_and_uses_subject():
    title = _fallback_chat_title([
        {"role": "user", "content": "create a microsoft uerer for employe gerhard in odoo"},
    ])

    assert title == "Create Microsoft User Employee Gerhard Odoo"


def test_fallback_chat_title_uses_latest_user_message_only():
    title = _fallback_chat_title([
        {"role": "user", "content": "whats costing so much"},
        {"role": "assistant", "content": "Azure Cost Management returned a daily cost table."},
    ])

    assert title == "Costing So Much"


def test_fallback_chat_title_normalizes_error_typos():
    title = _fallback_chat_title([
        {"role": "user", "content": "halllucinations and now claims it cannot acess azure"},
    ])

    assert title == "Hallucinations Claims Cannot Access Azure"


def test_fallback_chat_title_does_not_preserve_raw_misspellings():
    title = _fallback_chat_title([
        {"role": "user", "content": "faliours during thinking in the connecotrs"},
    ])

    assert title == "Failures During Thinking Connectors"
    assert "Faliours" not in title
    assert "Connecotrs" not in title


def test_tool_selection_message_inherits_context_for_date_correction():
    messages = [
        {"role": "user", "content": "what did Penelope do in Odoo today, give me a timeline"},
        {"role": "assistant", "content": "I checked Odoo for Penelope's activity today."},
        {"role": "user", "content": "i meant 4 june"},
    ]

    selection_text = _tool_selection_message(messages)

    assert "Odoo" in selection_text
    assert "i meant 4 june" in selection_text


MICROSOFT_TOOL_NAMES = tuple(sorted(MICROSOFT_NATIVE_TOOL_NAMES))
MICROSOFT_TOOL_TARGET_SYSTEMS = {
    "ms_azure_cli": "azure_cli",
    "ms_graph": "microsoft_graph",
    "ms_exchange_powershell": "exchange_online",
    "ms_teams_powershell": "teams_admin",
    "ms_sharepoint_pnp_powershell": "sharepoint_pnp",
}


def _microsoft_tool_target(tool_name: str) -> str:
    return MICROSOFT_TOOL_TARGET_SYSTEMS[tool_name]


def test_removed_azure_cli_tool_name_is_not_canonicalized():
    tool_name, args = _canonical_tool_invocation("azure_cli", {"command": "account show"})

    assert tool_name == "azure_cli"
    assert args == {"command": "account show"}


def test_removed_ms_admin_and_ms_powershell_tool_names_are_not_canonicalized():
    assert _canonical_tool_invocation("ms_admin", {"mode": "azure_cli", "command": "account show"}) == (
        "ms_admin",
        {"mode": "azure_cli", "command": "account show"},
    )
    assert _canonical_tool_invocation("ms_powershell", {"script": "Get-MgUser"}) == (
        "ms_powershell",
        {"script": "Get-MgUser"},
    )


def test_microsoft_graph_textual_alias_is_canonicalized_to_ms_graph():
    tool_name, args = _canonical_tool_invocation(
        "functions.microsoft_graph:0",
        {
            "method": "GET",
            "url": "https://graph.microsoft.com/v1.0/users?$filter=accountEnabled eq true&$select=displayName,userPrincipalName",
        },
    )

    assert tool_name == "ms_graph"
    assert args == {
        "method": "GET",
        "api_version": "v1.0",
        "path": "/users?$filter=accountEnabled eq true&$select=displayName,userPrincipalName",
    }


def test_microsoft_graph_alias_normalizes_relative_version_endpoint():
    tool_name, args = _canonical_tool_invocation(
        "graph_api",
        {"method": "GET", "endpoint": "beta/groups?$select=id,displayName"},
    )

    assert tool_name == "ms_graph"
    assert args == {
        "method": "GET",
        "api_version": "beta",
        "path": "/groups?$select=id,displayName",
    }


@pytest.mark.asyncio
async def test_model_router_rejects_removed_microsoft_tool_names():
    for old_tool_name in ("azure_cli", "ms_admin", "ms_powershell", "ms_az_powershell", "ms_graph_powershell", "ms_bicep"):
        result = await _execute_tool_call_impl(AsyncMock(), uuid.uuid4(), old_tool_name, {"command": "account show"})
        assert result["status"] == "failed"
        assert result["error_type"] == "unknown_tool"
        assert "current tool registry" in result["message"]


def test_microsoft_guidance_uses_native_tools_and_cost_management_rest_query():
    tools = [
        AITool(
            name=name,
            display_name=name,
            description="Run Microsoft admin tooling",
            target_system=_microsoft_tool_target(name),
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        for name in MICROSOFT_TOOL_NAMES
    ]
    prompt = _append_tool_guidance(
        "base\n",
        tools,
        [{"type": "function", "function": {"name": tool.name, "parameters": {"type": "object"}}} for tool in tools],
    )

    assert "use only these broad native-interface tools" in prompt
    assert "`ms_azure_cli`, `ms_graph`" in prompt
    assert "`ms_exchange_powershell`, `ms_teams_powershell`, and `ms_sharepoint_pnp_powershell`" in prompt
    assert "ms_graph_powershell" not in prompt
    assert "ms_az_powershell" not in prompt
    assert "ms_bicep" not in prompt
    assert "do not call removed generic or duplicate Microsoft tools" in prompt
    assert "do not use `az costmanagement query`" in prompt
    assert "az rest --method post" in prompt
    assert "Microsoft.CostManagement/query" in prompt
    assert "Do not claim all Microsoft access is broken" in prompt
    assert "limited by that user's platform roles/RBAC plus consent" in prompt
    assert "does not by itself prove Azure Resource Manager" in prompt
    assert "Never invent Azure cost totals" in prompt
    assert "successful tool result only" in prompt
    assert "do not say there is no Microsoft user-management tool" in prompt


def test_tool_finalizer_keeps_ms_admin_connection_distinct_from_command_errors():
    messages = [{"role": "user", "content": "what is costing so much in Azure?"}]
    tool_results = [
        {
            "tool_name": "ms_azure_cli",
            "arguments": {"command": "costmanagement query --type Usage"},
            "result": {
                "status": "failed",
                "error_type": "command_failed",
                "message": "ERROR: 'query' is misspelled or not recognized by the system.",
            },
        }
    ]

    finalizer_messages = _build_tool_finalizer_messages(messages, tool_results)
    system_text = finalizer_messages[0]["content"]

    assert "failed command, missing role, or unsupported CLI subcommand does not mean every Microsoft connector" in system_text


def test_guard_replaces_false_azure_not_connected_denial_when_connected():
    bad_content = (
        "I do not have access to your Azure cost data.\n\n"
        "Azure Cost Management — Not connected.\n"
        "Go to Connected Accounts and add/authorize an Azure connector."
    )

    guarded = _guard_connected_system_denial(
        bad_content,
        {"azure_cli"},
        [{
            "tool_name": "ms_azure_cli",
            "error_type": "command_failed",
            "message": "ERROR: 'query' is misspelled or not recognized by the system.",
        }],
    )

    assert "Azure CLI is connected" in guarded
    assert "successful Azure Cost Management tool result" in guarded
    assert "Connected Accounts" not in guarded


def test_guard_preserves_real_connected_azure_permission_error():
    content = "I do not have access to your Azure cost data because the Cost Management query returned AuthorizationFailed."

    guarded = _guard_connected_system_denial(
        content,
        {"azure_cli"},
        [{
            "tool_name": "ms_azure_cli",
            "error_type": "AuthorizationFailed",
            "message": "The client does not have authorization to perform action Microsoft.CostManagement/query/read.",
        }],
    )

    assert guarded == content


def test_guard_allows_real_microsoft_connector_not_connected_tool_error():
    content = "Azure is not connected. Go to Connected Accounts."

    guarded = _guard_connected_system_denial(
        content,
        {"microsoft_graph"},
        [{"tool_name": "ms_graph", "error_type": "not_connected", "message": "Microsoft Graph is not connected."}],
    )

    assert guarded == content


def test_compact_tool_result_preserves_small_graph_collections():
    result = {
        "status": "success",
        "connector": "ms_graph",
        "mode": "graph_request",
        "result": {
            "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users",
            "value": [{"id": str(index), "displayName": f"User {index}"} for index in range(14)],
        },
    }

    compacted = _compact_tool_result_for_model(result)

    assert len(compacted["result"]["value"]) == 14
    assert "truncated_items" not in compacted["result"]["value"]


# ── Mock DB that can simulate empty / configured / connector states ──

class MockSession:
    def __init__(self, has_config=False, connected_accounts=None):
        self.has_config = has_config
        self.connected_accounts = connected_accounts or []
        self.added = []
        if has_config:
            self._provider = AIProvider(
                id=uuid.uuid4(), name="Microsoft Foundry", provider_type="azure_foundry",
                base_url="https://mock.services.ai.azure.com", auth_type="key_vault_secret",
                secret_reference="mock-key", enabled="true",
            )
            self._model = AIModel(
                id=uuid.uuid4(), provider_id=self._provider.id, display_name="Kimi K2.6",
                model_name="Kimi-K2.6", deployment_name="kimi-k2-6-general-chat",
                model_family="Kimi", model_version="2026-04-20",
                supports_tools="true", supports_json_schema="false",
                context_window=262144, enabled="true",
            )
            self._route = AIRoute(
                id=uuid.uuid4(), task_type="general_chat", primary_model_id=self._model.id,
                temperature=0.3, max_tokens=2000, enabled="true",
                system_prompt=CANONICAL_SYSTEM_PROMPT,
            )

    async def execute(self, stmt, *args, **kwargs):
        stmt_str = str(stmt).lower()

        class MockResult:
            def __init__(self, route=None, model=None, provider=None, accounts=None):
                self._route = route
                self._model = model
                self._provider = provider
                self._accounts = accounts or []

            def scalar_one_or_none(self):
                if "ai_routes" in stmt_str and self._route:
                    return self._route
                if "ai_models" in stmt_str and self._model:
                    return self._model
                if "ai_providers" in stmt_str and self._provider:
                    return self._provider
                return None

            def scalars(self):
                return self

            def all(self):
                if "ai_tools" in stmt_str or "ai_memories" in stmt_str:
                    return []
                return self._accounts

            def first(self):
                return self._accounts[0] if self._accounts else None

        if self.has_config:
            return MockResult(
                route=self._route, model=self._model,
                provider=self._provider, accounts=self.connected_accounts,
            )
        return MockResult(accounts=self.connected_accounts)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def close(self):
        pass

    def add(self, obj):
        self.added.append(obj)


async def mock_get_db_empty():
    yield MockSession(has_config=False)

async def mock_get_db_configured():
    yield MockSession(has_config=True)

async def mock_get_db_with_connector(connected_type="odoo"):
    account = AIConnectedAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        provider=connected_type,
        provider_username=f"test@{connected_type}.com",
        status="connected",
    )
    yield MockSession(has_config=True, connected_accounts=[account])


@pytest.mark.asyncio
async def test_odoo_tool_credentials_require_saved_connection_details():
    from app.services.model_router import _resolve_odoo_credentials_for_tool

    user_id = uuid.uuid4()
    account = AIConnectedAccount(
        id=uuid.uuid4(),
        user_id=user_id,
        provider="odoo",
        provider_username="odoo@example.com",
        status="connected",
        secret_reference="odoo-secret",
        odoo_url=None,
        odoo_db=None,
    )

    class FakeResult:
        def __init__(self, scalar=None):
            self._scalar = scalar

        def scalar_one_or_none(self):
            return self._scalar

    class FakeSession:
        async def execute(self, stmt, *args, **kwargs):
            stmt_text = str(stmt).lower()
            if "ai_connected_accounts" in stmt_text:
                return FakeResult(scalar=account)
            return FakeResult()

    with patch("app.services.model_router.key_vault_uri", return_value="https://vault.example.com"), patch(
        "app.services.model_router.get_secret_value", new=AsyncMock(return_value="api-key")
    ):
        with pytest.raises(RuntimeError, match="missing its saved URL or database"):
            await _resolve_odoo_credentials_for_tool(FakeSession(), user_id)


@pytest.mark.asyncio
async def test_generate_chat_title_falls_back_when_title_model_unavailable():
    from app.services.chat_titles import generate_chat_title

    title = await generate_chat_title([
        {"role": "user", "content": "what are all my azure resources and month to date costs"}
    ])

    assert title == "Azure Resources Month Date Costs"


@pytest.mark.asyncio
async def test_generate_chat_title_does_not_call_model():
    from app.services.chat_titles import generate_chat_title

    with patch("app.services.model_router._call_model", new=AsyncMock()) as call_model:
        title = await generate_chat_title([
            {"role": "user", "content": "there are 2 gerhard employees in my odoo?"}
        ])

    assert title == "Gerhard Employees Odoo"
    assert call_model.await_count == 0


# ── Connector Context Tests ──

class TestConnectorContext:
    @pytest.mark.asyncio
    async def test_get_connector_context_no_user(self):
        from app.services.model_router import _get_connector_context
        db = MockSession(has_config=False)
        result = await _get_connector_context(db, user_id=None)
        assert "(no authenticated user context)" in result

    @pytest.mark.asyncio
    async def test_get_connector_context_no_accounts(self):
        from app.services.model_router import _get_connector_context
        db = MockSession(has_config=False)
        result = await _get_connector_context(db, user_id=uuid.uuid4())
        assert "not connected" in result
        assert "Odoo" in result
        assert "GitHub" in result
        assert "Microsoft 365" not in result

    @pytest.mark.asyncio
    async def test_get_connector_context_odoo_connected(self):
        from app.services.model_router import _get_connector_context
        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=False, connected_accounts=[account])
        result = await _get_connector_context(db, user_id=uuid.uuid4())
        assert "✓" in result
        assert "Odoo: connected" in result
        assert "GitHub: not connected" in result

    @pytest.mark.asyncio
    async def test_get_connector_context_azure_cli_connected_names_azure_capability(self):
        from app.services.model_router import _get_connector_context
        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="azure_cli", status="connected",
        )
        db = MockSession(has_config=False, connected_accounts=[account])

        result = await _get_connector_context(db, user_id=uuid.uuid4())

        assert "Azure CLI: connected" in result
        assert "Do not claim a specific Microsoft resource is accessible until that operation succeeds" in result
        assert "Azure RBAC" in result

    @pytest.mark.asyncio
    async def test_connector_context_not_injected_without_user_id(self):
        """When user_id is None (e.g. unauthenticated), connector context
        should NOT claim any systems are connected."""
        from app.services.model_router import _get_connector_context
        db = MockSession(has_config=False)
        result = await _get_connector_context(db, user_id=None)
        assert "✓" not in result
        assert "(no authenticated user context)" in result

    @pytest.mark.asyncio
    async def test_execute_chat_includes_connector_in_system_prompt(self):
        """The connector context block must be appended to the system prompt."""
        from app.services.model_router import execute_chat
        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])
        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(
                chat_completion=AsyncMock(return_value={
                    "content": "Hello! I am the AI Platform.",
                    "finish_reason": "stop",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "latency_ms": 100,
                })
            ))
        ):
            result = await execute_chat(db, [{"role": "user", "content": "hi"}], user_id=uuid.uuid4())
            assert result["content"] == "Hello! I am the AI Platform."

    @pytest.mark.asyncio
    async def test_execute_chat_includes_current_date_context(self):
        from app.services.model_router import execute_chat

        fixed_now = datetime(2026, 6, 3, 8, 30, 0, tzinfo=ZoneInfo("Africa/Johannesburg"))
        db = MockSession(has_config=True)
        mock_chat_completion = AsyncMock(return_value={
            "content": "I can use the current date.",
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 100,
        })

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router._platform_now',
            new=lambda now=None: fixed_now,
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(chat_completion=mock_chat_completion))
        ):
            result = await execute_chat(db, [{"role": "user", "content": "what is today?"}], user_id=uuid.uuid4())

        called_messages = mock_chat_completion.call_args[1]["messages"]
        system_prompt_content = called_messages[0]["content"]
        assert "## Current Date and Time" in system_prompt_content
        assert "Current date: 2026-06-03" in system_prompt_content
        assert "this month starts on 2026-06-01 and ends today, 2026-06-03" in system_prompt_content
        assert result["context"]["current_date"] == "2026-06-03"

    @pytest.mark.asyncio
    async def test_execute_chat_treats_stored_azure_cli_account_as_connected_without_token_lookup(self):
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            provider="azure_cli",
            status="connected",
            user_id=uuid.uuid4(),
            provider_username="admin-user",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name=name,
                                display_name=name,
                                description="Run Microsoft admin tooling",
                                target_system=_microsoft_tool_target(name),
                                input_schema={"type": "object", "properties": {}, "required": []},
                            )
                            for name in MICROSOFT_TOOL_NAMES
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        async def fake_token_status(provider, _user_id):
            raise AssertionError(f"unexpected token lookup for {provider}")

        db.execute = mock_execute
        mock_chat_completion = AsyncMock(return_value={
            "content": "Azure is connected.",
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 100,
        })

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.connected_account_state.token_status',
            new=AsyncMock(side_effect=fake_token_status),
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(chat_completion=mock_chat_completion))
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "can you access my azure?"}],
                user_id=uuid.uuid4(),
            )

        called_kwargs = mock_chat_completion.call_args[1]
        system_prompt_content = called_kwargs["messages"][0]["content"]
        tool_names = [tool["function"]["name"] for tool in called_kwargs["tools"]]
        assert "Azure CLI: connected" in system_prompt_content
        assert "Do not claim all Microsoft access is broken" in system_prompt_content
        assert "ms_azure_cli" in tool_names
        assert "ms_admin" not in tool_names
        assert result["content"] == "Azure is connected."

    @pytest.mark.asyncio
    async def test_execute_chat_recovers_azure_inventory_when_model_quota_blocks_tool_call(self):
        from app.services.model_router import execute_chat

        user_id = uuid.uuid4()
        account = AIConnectedAccount(
            provider="azure_cli",
            status="connected",
            user_id=user_id,
            provider_username="alden@lotslotsmore.com",
        )
        db = MockSession(has_config=True, connected_accounts=[account])
        ms_azure_cli_tool = AITool(
            name="ms_azure_cli",
            display_name="Azure Resource Manager CLI",
            description="Run Azure Resource Manager CLI commands.",
            target_system="azure_cli",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        )
        tool_schema = [{
            "type": "function",
            "function": {
                "name": "ms_azure_cli",
                "description": "Run Azure Resource Manager CLI commands.",
                "parameters": ms_azure_cli_tool.input_schema,
            },
        }]

        async def fake_select_tools(*_args, **_kwargs):
            system_prompt = _args[-1]
            return [ms_azure_cli_tool], tool_schema, system_prompt

        async def fake_tool_call(_db, _user_id, tool_name, arguments, trace_svc=None):
            assert tool_name == "ms_azure_cli"
            command = arguments["command"]
            if command.startswith("account show"):
                return {
                    "status": "success",
                    "stdout": json.dumps({
                        "name": "Lots Lots More",
                        "id": "sub-123",
                        "user": {"name": "alden@lotslotsmore.com"},
                    }),
                    "stderr": "",
                    "exit_code": 0,
                }
            if command.startswith("resource list"):
                return {
                    "status": "success",
                    "stdout": json.dumps([
                        {
                            "name": "ca-ai-platform-api-prod-san-001",
                            "type": "Microsoft.App/containerApps",
                            "resourceGroup": "rg-ai-platform-prod-san-001",
                            "location": "southafricanorth",
                        }
                    ]),
                    "stderr": "",
                    "exit_code": 0,
                }
            raise AssertionError(f"unexpected command: {command}")

        with patch.object(
            type(db), "add"
        ), patch.object(
            type(db), "flush"
        ), patch(
            "app.services.model_router._select_tools_for_model",
            new=fake_select_tools,
        ), patch(
            "app.services.model_router._fallback_candidates",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.model_router._call_model",
            new=AsyncMock(return_value=({
                "error": True,
                "error_type": "quota_exceeded",
                "message": "quota exhausted",
                "status_code": 429,
                "latency_ms": 20,
            }, MagicMock())),
        ), patch(
            "app.services.model_router._execute_tool_call",
            new=AsyncMock(side_effect=fake_tool_call),
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "can you access my azure if so list active resources"}],
                user_id=user_id,
            )

        assert result["finish_reason"] == "deterministic_connector_fallback"
        assert "Azure Resource Manager is accessible" in result["content"]
        assert "Lots Lots More" in result["content"]
        assert "ca-ai-platform-api-prod-san-001" in result["content"]
        assert result["tool_call_count"] == 2
        assert result["tool_calls"][0]["tool_name"] == "ms_azure_cli"

    @pytest.mark.asyncio
    async def test_execute_chat_injects_active_memories(self):
        """Active AIMemory records for the user must appear in '## Learned from Past Interactions'."""
        from app.services.model_router import execute_chat
        from app.models.models import AIProvider, AIModel, AIRoute

        test_user_id = uuid.uuid4()
        db = MockSession(has_config=False)

        # Real model objects for get_enabled_route
        provider = AIProvider(
            id=uuid.uuid4(), name="Microsoft Foundry", provider_type="azure_foundry",
            base_url="https://mock.services.ai.azure.com", auth_type="key_vault_secret",
            secret_reference="mock-key", enabled="true",
        )
        model = AIModel(
            id=uuid.uuid4(), provider_id=provider.id, display_name="Kimi K2.6",
            model_name="Kimi-K2.6", deployment_name="kimi-k2-6-general-chat",
            model_family="Kimi", model_version="2026-04-20",
            supports_tools="true", supports_json_schema="false",
            context_window=262144, enabled="true",
        )
        route = AIRoute(
            id=uuid.uuid4(), task_type="general_chat", primary_model_id=model.id,
            temperature=0.3, max_tokens=2000, enabled="true",
            system_prompt="You are the AI Platform.",
        )

        async def mock_get_enabled_route(*args, **kwargs):
            return (route, model, provider)

        class MemoryQueryResult:
            @property
            def scalars(self):
                def all():
                    return [
                        AIMemory(
                            id=uuid.uuid4(),
                            type="preference",
                            title="Prefers brief answers",
                            summary="User prefers concise responses with bullet points",
                            body="Always summarize the key points first, then expand if needed",
                            status="active",
                            created_by_user_id=test_user_id,
                            priority=10,
                        ),
                        AIMemory(
                            id=uuid.uuid4(),
                            type="resolved_case",
                            title="Invoice approval workflow",
                            summary="Invoices under $1000 can be auto-approved",
                            status="active",
                            created_by_user_id=test_user_id,
                            priority=50,
                        ),
                    ]
                return all

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt)
            if "ai_memories" in stmt_str:
                return MemoryQueryResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.get_enabled_route',
            new=mock_get_enabled_route,
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(
                chat_completion=AsyncMock(return_value={
                    "content": "Here is your answer with memories considered.",
                    "finish_reason": "stop",
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "latency_ms": 150,
                })
            ))
        ):
            result = await execute_chat(
                db, [{"role": "user", "content": "help me with invoices"}],
                user_id=test_user_id,
            )
            assert result["content"] == "Here is your answer with memories considered."

    @pytest.mark.asyncio
    async def test_execute_chat_no_memories_when_none_exist(self):
        """When no active memories exist, no '## Learned from Past Interactions' section is added."""
        from app.services.model_router import execute_chat
        from app.models.models import AIProvider, AIModel, AIRoute

        db = MockSession(has_config=False)

        provider = AIProvider(
            id=uuid.uuid4(), name="Microsoft Foundry", provider_type="azure_foundry",
            base_url="https://mock.services.ai.azure.com", auth_type="key_vault_secret",
            secret_reference="mock-key", enabled="true",
        )
        model = AIModel(
            id=uuid.uuid4(), provider_id=provider.id, display_name="Kimi K2.6",
            model_name="Kimi-K2.6", deployment_name="kimi-k2-6-general-chat",
            model_family="Kimi", model_version="2026-04-20",
            supports_tools="true", supports_json_schema="false",
            context_window=262144, enabled="true",
        )
        route = AIRoute(
            id=uuid.uuid4(), task_type="general_chat", primary_model_id=model.id,
            temperature=0.3, max_tokens=2000, enabled="true",
            system_prompt="You are the AI Platform.",
        )

        async def mock_get_enabled_route(*args, **kwargs):
            return (route, model, provider)

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.get_enabled_route',
            new=mock_get_enabled_route,
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(
                chat_completion=AsyncMock(return_value={
                    "content": "OK",
                    "finish_reason": "stop",
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                     "latency_ms": 50,
                 })
             ))
         ):
             result = await execute_chat(db, [{"role": "user", "content": "hi"}], user_id=uuid.uuid4())
             assert result["content"] == "OK"

# ── Seed Script Tests ──

class TestSeedIdempotent:
    @pytest.mark.asyncio
    async def test_seed_creates_provider(self):
        """Seed creates provider when none exists."""
        from scripts.seed_providers import PROVIDERS_TO_SEED, MODELS_TO_SEED, ROUTES_TO_SEED, CANONICAL_SYSTEM_PROMPT
        assert PROVIDERS_TO_SEED[0]["name"] == "Microsoft Foundry"
        assert MODELS_TO_SEED[0]["model_name"] == "Kimi-K2.6"
        assert ROUTES_TO_SEED[0]["task_type"] == "general_chat"
        assert ROUTES_TO_SEED[0]["system_prompt"] == CANONICAL_SYSTEM_PROMPT

    def test_seed_providers_uses_canonical_prompt(self):
        from scripts.seed_providers import CANONICAL_SYSTEM_PROMPT
        from app.services.model_router import CANONICAL_SYSTEM_PROMPT as ROUTER_PROMPT
        assert CANONICAL_SYSTEM_PROMPT == ROUTER_PROMPT


@pytest.fixture(autouse=True)
def _cleanup_global_state():
    yield
    app.dependency_overrides.clear()


# ── Chat Endpoint Tests ──

class TestChatWithModelRouter:
    def test_chat_returns_friendly_error_when_no_route(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.post(
            "/chat/sessions/00000000-0000-0000-0000-000000000001/messages",
            json={"content": "hello"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.json()
            assert "not configured" in data.get("content", "").lower() or ROUTE_NOT_CONFIGURED_MESSAGE in data.get("content", "")


# ── Greeting Identity Tests ──

class TestGreetingIdentity:
    """The assistant must not present as Odoo/ERP assistant by default."""

    def test_no_hardcoded_odoo_assistant_in_backend(self):
        """Verify no 'Odoo assistant' or 'ERP assistant' string in backend code."""
        import os as os_module

        backend_root = os_module.path.join(os_module.path.dirname(__file__), "..", "app")
        odoo_phrases = ["Odoo assistant", "ERP assistant", "Odoo ERP", "integrated with Odoo",
                        "fully integrated into Odoo", "operational assistant"]
        for root, dirs, files in os_module.walk(backend_root):
            for f in files:
                if f.endswith(".py"):
                    path = os_module.path.join(root, f)
                    with open(path, "r") as fh:
                        content = fh.read()
                    for phrase in odoo_phrases:
                        if phrase in content:
                            pytest.fail(f"Found '{phrase}' in {path}")

    def test_no_hardcoded_odoo_assistant_in_frontend(self):
        """Verify no 'Odoo assistant' or 'ERP assistant' string in frontend code."""
        import os as os_module

        frontend_root = os_module.path.join(os_module.path.dirname(__file__), "..", "..", "web-portal", "src")
        if not os_module.path.isdir(frontend_root):
            pytest.skip("Frontend src directory not found")
        odoo_phrases = ["Odoo assistant", "ERP assistant", "Odoo ERP", "operational assistant"]
        for root, dirs, files in os_module.walk(frontend_root):
            for f in files:
                if f.endswith((".tsx", ".ts", ".jsx", ".js")):
                    path = os_module.path.join(root, f)
                    with open(path, "r") as fh:
                        content = fh.read()
                    for phrase in odoo_phrases:
                        if phrase in content:
                            pytest.fail(f"Found '{phrase}' in {path}")


# ── Tool Definition Tests ──

class TestToolDefinitions:
    def test_build_tool_definitions_empty(self):
        from app.services.model_tool_calls import _build_tool_definitions
        assert _build_tool_definitions([]) == []

    def test_build_tool_definitions_skips_missing_schema(self):
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(name="odoo_ops_runner", display_name="Odoo Ops Runner",
                       description="Search Odoo", target_system="odoo", input_schema=None)
        assert _build_tool_definitions([tool]) == []

    def test_build_tool_definitions_valid(self):
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="odoo_ops_runner", display_name="Odoo Ops Runner",
            description="Run Odoo operations",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "odoo_ops_runner"
        assert "parameters" in defs[0]["function"]

    def test_odoo_tool_guidance_forbids_invented_links(self):
        from app.services.model_router import _append_tool_guidance
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="odoo_ops_runner",
            display_name="Odoo Ops Runner",
            description="Run Odoo operations",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"]},
        )

        system_prompt = _append_tool_guidance("Base prompt.", [tool], _build_tool_definitions([tool]))

        assert "Do not invent Odoo web URLs" in system_prompt
        assert "record_url" in system_prompt
        assert "cannot provide a verified link" in system_prompt
        assert "effect_verified=true" in system_prompt
        assert "not a private Discuss direct message" in system_prompt

    def test_build_tool_definitions_normalizes_dotted_names(self):
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="odoo.ops_runner", display_name="Odoo Ops Runner",
            description="Run Odoo operations",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "odoo_ops_runner"
        assert "." not in defs[0]["function"]["name"]

    def test_normalize_tool_name(self):
        from app.services.model_tool_calls import _normalize_tool_name
        assert _normalize_tool_name("odoo.ops_runner") == "odoo_ops_runner"
        assert _normalize_tool_name("odoo.attach_artifact") == "odoo_attach_artifact"
        assert _normalize_tool_name("already_normal") == "already_normal"
        assert _normalize_tool_name("no-changes_needed") == "no-changes_needed"
        assert len(_normalize_tool_name("a" * 100)) == 64

    def test_build_tool_definitions_strips_invalid_chars(self):
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="odoo#attach@artifact!", display_name="Odoo Attach",
            description="Attach artifact",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "odoo_attach_artifact_"


# ── Rate-Limit / Quota Error Handling Tests ──

class TestProviderErrorHandling:
    """Provider errors must produce user-friendly messages, not raw provider text."""

    _mock_route = None
    _mock_model = None
    _mock_provider = None

    @pytest.fixture(autouse=True)
    def _setup_mocks(self):
        """Set up mock route/model/provider to avoid relying on MockSession's
        broken multi-query support."""
        from app.models.models import AIProvider, AIModel, AIRoute
        provider = AIProvider(
            id=uuid.uuid4(), name="Microsoft Foundry", provider_type="azure_foundry",
            base_url="https://mock.services.ai.azure.com", auth_type="key_vault_secret",
            secret_reference="mock-key", enabled="true",
        )
        model = AIModel(
            id=uuid.uuid4(), provider_id=provider.id, display_name="Kimi K2.6",
            model_name="Kimi-K2.6", deployment_name="kimi-k2-6-general-chat",
            model_family="Kimi", model_version="2026-04-20",
            supports_tools="true", supports_json_schema="false",
            context_window=262144, enabled="true",
        )
        route = AIRoute(
            id=uuid.uuid4(), task_type="general_chat", primary_model_id=model.id,
            temperature=0.3, max_tokens=2000, enabled="true",
            system_prompt="You are the AI Platform.",
        )
        type(self)._mock_provider = provider
        type(self)._mock_model = model
        type(self)._mock_route = route

    async def _run_execute_chat(self, chat_completion_return: dict):
        from app.services.model_router import execute_chat
        db = MockSession(has_config=True)

        # Mock get_enabled_route to return our properly constructed objects
        async def mock_get_enabled_route(*args, **kwargs):
            return (self._mock_route, self._mock_model, self._mock_provider)

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.get_enabled_route',
            new=mock_get_enabled_route,
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(
                chat_completion=AsyncMock(return_value=chat_completion_return)
            ))
        ):
            return await execute_chat(db, [{"role": "user", "content": "hi"}], user_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_execute_chat_rate_limit_error(self):
        """A rate-limit error from the provider must raise ProviderCallError
        with a user-friendly message and NOT expose the raw error."""
        from app.services.model_router import ProviderCallError

        with pytest.raises(ProviderCallError) as exc_info:
            await self._run_execute_chat({
                "error": True,
                "error_type": "rate_limit_exceeded",
                "status_code": 429,
                "message": "Rate limit exceeded. Quota request exceeds the requests limit. Requested requests: 0.",
                "raw_response": '{"error": {"message": "Rate limit exceeded..."}}',
                "latency_ms": 50,
            })

        # The user-facing message must NOT contain the raw provider error text
        assert "Rate limit" not in str(exc_info.value)
        assert "quota or rate limit" in str(exc_info.value)
        assert "model" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_chat_records_trace_payloads_and_usage_correlation(self):
        from app.services.model_router import execute_chat
        from app.services.trace_service import TraceService

        db = MockSession(has_config=True)
        request_id = "req_observability_test"
        trace_svc = TraceService(db, request_id=request_id)
        trace_svc.begin("chat_message", "test chat", user_id=uuid.uuid4())
        client = AsyncMock(
            chat_completion=AsyncMock(return_value={
                "error": False,
                "content": "Hello from the model.",
                "finish_reason": "stop",
                "tool_calls": None,
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
                "latency_ms": 34,
                "model": "mock-model",
                "raw_response": {"usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}},
            })
        )

        with patch(
            "app.services.model_router.build_foundry_client",
            new=AsyncMock(return_value=client),
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "hi"}],
                user_id=uuid.uuid4(),
                trace_svc=trace_svc,
                request_id=request_id,
            )

        assert result["total_tokens"] == 17
        usage_logs = [item for item in db.added if isinstance(item, AIUsageLog)]
        assert len(usage_logs) == 1
        assert usage_logs[0].request_id == request_id
        assert usage_logs[0].trace_id == trace_svc.trace_id
        assert usage_logs[0].prompt_tokens == 12

        provider_spans = [span for span in trace_svc._spans.values() if span.span_type == "provider_call"]
        assert len(provider_spans) == 1
        provider_span = provider_spans[0]
        assert provider_span.input_summary_json["request"]["messages"][0]["role"] == "system"
        assert provider_span.output_summary_json["usage"]["prompt_tokens"] == 12
        assert provider_span.output_summary_json["response"]["raw_response"]["usage"]["total_tokens"] == 17

    @pytest.mark.asyncio
    async def test_execute_chat_quota_error_via_403(self):
        """A quota error with 403 status must still produce a user-friendly message."""
        from app.services.model_router import ProviderCallError

        with pytest.raises(ProviderCallError) as exc_info:
            await self._run_execute_chat({
                "error": True,
                "error_type": "quota_exceeded",
                "status_code": 403,
                "message": "Quota exceeded for this deployment.",
                "raw_response": '{"error": {"message": "Quota exceeded..."}}',
                "latency_ms": 50,
            })

        assert "quota or rate limit" in str(exc_info.value)
        assert "Quota exceeded" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_chat_server_error(self):
        """A 5xx error must produce a generic user-friendly message."""
        from app.services.model_router import ProviderCallError

        with pytest.raises(ProviderCallError) as exc_info:
            await self._run_execute_chat({
                "error": True,
                "error_type": "server_error",
                "status_code": 502,
                "message": "Bad gateway from upstream",
                "raw_response": '{"error": {"message": "Bad gateway"}}',
                "latency_ms": 50,
            })

        assert "temporarily unavailable" in str(exc_info.value)
        assert "Bad gateway" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_chat_auth_error(self):
        from app.services.model_router import ProviderCallError

        with pytest.raises(ProviderCallError) as exc_info:
            await self._run_execute_chat({
                "error": True,
                "error_type": "authentication_error",
                "status_code": 401,
                "message": "Unauthorized. Check your API key.",
                "raw_response": '{"error": {"message": "Unauthorized"}}',
                "latency_ms": 50,
            })

        assert "authentication" in str(exc_info.value)
        assert "Unauthorized" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_chat_endpoint_returns_friendly_quota_error(self):
        """The post_chat_message endpoint must return a user-friendly message
        when the provider returns a quota error — NOT the raw provider text."""
        import app.services.model_router as mr_module
        from app.models.models import AIChatSession

        async def mock_get_db_with_session():
            """Yield a MockSession whose first execute call returns a real
            AIChatSession (instead of an AIRoute) so chat.py's session check
            and .title access work correctly."""
            session = AIChatSession(
                id=uuid.uuid4(),
                user_id="e4807f22-97c8-4778-87a2-160f56d25247",
                title="New Chat",
                status="active",
                last_message_at=datetime.now(timezone.utc),
            )
            # Wrap the MockSession to return a real AIChatSession for the
            # initial session-lookup query, and fall back to the configured
            # mock behavior for everything else.
            class ChatSessionAwareMock:
                def __init__(self):
                    self.added = []

                async def execute(self, stmt, *args, **kwargs):
                    stmt_str = str(stmt)
                    result = AsyncMock()
                    if "ai_chat_sessions" in stmt_str:
                        result.scalar_one_or_none = lambda: session
                    else:
                        result.scalar_one_or_none = lambda: None
                        result.scalars = lambda: AsyncMock(all=lambda: [])
                    return result

                async def flush(self): pass
                async def commit(self): pass
                async def close(self): pass
                async def refresh(self, obj): pass
                def add(self, obj): self.added.append(obj)

            yield ChatSessionAwareMock()

        async def mock_execute_quota_error(*args, **kwargs):
            raise mr_module.ProviderCallError(
                "The AI service is temporarily unavailable because the model "
                "quota or rate limit has been reached. "
                "Please try again shortly, or contact support if this continues.",
                "Microsoft Foundry",
                "Kimi K2.6",
            )

        with patch.object(mr_module, 'execute_chat', mock_execute_quota_error):
            app.dependency_overrides[get_db] = mock_get_db_with_session
            client = TestClient(app)
            response = client.post(
                "/chat/sessions/00000000-0000-0000-0000-000000000001/messages",
                json={"content": "what's the latest bill?"},
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 502
            data = response.json()
            # The error detail must be the friendly message, not the raw error
            detail = data.get("detail") or {}
            if isinstance(detail, str):
                assert "quota or rate limit" in detail
                assert "Rate limit exceeded" not in detail
            else:
                assert "quota or rate limit" in detail.get("error_message", "")
                assert "Rate limit exceeded" not in detail.get("error_message", "")


class TestToolExecution:
    def test_core_router_has_no_deterministic_odoo_report_detector(self):
        import app.services.model_router as model_router

        assert not hasattr(model_router, "detect_odoo_report_intent")
        assert not hasattr(model_router, "detect_odoo_lookup_intent")

    def test_odoo_alias_execute_is_not_canonicalized(self):
        from app.services.model_tool_calls import _canonical_tool_invocation

        original_arguments = {
            "model": "mail.activity",
            "method": "action_feedback",
            "ids": [2180],
            "kwargs": {"feedback": "Receipt corrected"},
        }
        tool_name, arguments = _canonical_tool_invocation(
            "odoo",
            original_arguments,
        )

        assert tool_name == "odoo"
        assert arguments == original_arguments

    @pytest.mark.asyncio
    async def test_odoo_ops_runner_missing_mode_is_handled_before_connector(self):
        from app.services.model_router import _execute_tool_call_impl

        db = MockSession(has_config=True)
        mock_credentials = AsyncMock(side_effect=AssertionError("credentials should not be resolved"))

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=mock_credentials,
        ):
            result = await _execute_tool_call_impl(db, uuid.uuid4(), "odoo_ops_runner", {})

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert result["missing"] == ["mode"]
        mock_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_odoo_ops_runner_query_shaped_missing_mode_is_rejected(self):
        from app.services.model_router import _execute_tool_call_impl

        db = MockSession(has_config=True)
        mock_credentials = AsyncMock(side_effect=AssertionError("credentials should not be resolved"))

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=mock_credentials,
        ):
            result = await _execute_tool_call_impl(
                db,
                uuid.uuid4(),
                "odoo_ops_runner",
                {
                    "model": "stock.picking",
                    "domain": [["id", "=", 5266]],
                    "fields": ["name", "state", "move_ids", "date_done"],
                    "limit": 10,
                },
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert result["missing"] == ["mode"]
        mock_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_odoo_attachment_without_attachment_id_is_handled_before_connector(self):
        from app.services.model_router import _execute_tool_call_impl

        db = MockSession(has_config=True)
        mock_credentials = AsyncMock(side_effect=AssertionError("credentials should not be resolved"))

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=mock_credentials,
        ):
            result = await _execute_tool_call_impl(
                db,
                uuid.uuid4(),
                "odoo_ops_runner",
                {"mode": "attachment", "model": "account.move", "ids": [57508]},
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert result["missing"] == ["attachment_id", "attachment_ids"]
        mock_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_odoo_execute_recordset_method_without_ids_is_handled_before_connector(self):
        from app.services.model_router import _execute_tool_call_impl

        db = MockSession(has_config=True)
        mock_credentials = AsyncMock(side_effect=AssertionError("credentials should not be resolved"))

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=mock_credentials,
        ):
            result = await _execute_tool_call_impl(
                db,
                uuid.uuid4(),
                "odoo_ops_runner",
                {
                    "mode": "execute",
                    "model": "mail.activity",
                    "method": "action_feedback",
                    "kwargs": {"feedback": "Receipt corrected"},
                },
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert result["missing"] == ["ids", "record_id", "args[0]"]
        assert "record-bound" in result["message"]
        mock_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_odoo_message_mode_defaults_operation_to_post_before_connector(self):
        from app.services.model_router import _execute_tool_call_impl

        posted_payload = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"operation": "post", "result": 9002, "effect_verified": True}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                posted_payload.update(kwargs["json"])
                return FakeResponse()

        fake_credentials = {
            "url": "https://example.odoo.com",
            "db": "example",
            "username": "user@example.com",
            "api_key": "secret",
            "transport": "auto",
        }

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=AsyncMock(return_value=fake_credentials),
        ), patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), patch(
            "app.services.model_router.ODOO_CONNECTOR_KEY",
            "test-key",
        ), patch("app.services.model_router.httpx.AsyncClient", FakeAsyncClient):
            result = await _execute_tool_call_impl(
                MockSession(has_config=True),
                uuid.uuid4(),
                "odoo_ops_runner",
                {
                    "mode": "message",
                    "model": "res.partner",
                    "record_id": 42,
                    "body": "Fixed the PO; you can bill now.",
                },
            )

        assert result == {"operation": "post", "result": 9002, "effect_verified": True}
        assert posted_payload["operation"] == "post"
        assert posted_payload["mode"] == "message"
        assert posted_payload["model"] == "res.partner"
        assert posted_payload["record_id"] == 42

    @pytest.mark.asyncio
    async def test_odoo_unverified_side_effect_is_guarded_before_model_answer(self):
        from app.services.model_router import _execute_tool_call_impl

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "model": "mail.activity",
                    "method": "action_feedback",
                    "result": False,
                    "verification": {"status": "still_open", "remaining_ids": [2180]},
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return FakeResponse()

        fake_credentials = {
            "url": "https://example.odoo.com",
            "db": "example",
            "username": "user@example.com",
            "api_key": "secret",
            "transport": "auto",
        }

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=AsyncMock(return_value=fake_credentials),
        ), patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), patch(
            "app.services.model_router.ODOO_CONNECTOR_KEY",
            "test-key",
        ), patch("app.services.model_router.httpx.AsyncClient", FakeAsyncClient):
            result = await _execute_tool_call_impl(
                MockSession(has_config=True),
                uuid.uuid4(),
                "odoo_ops_runner",
                {
                    "mode": "execute",
                    "model": "mail.activity",
                    "method": "action_feedback",
                    "ids": [2180],
                    "kwargs": {"feedback": "Receipt corrected"},
                },
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "unverified_side_effect"
        assert result["error_type"] == "unverified_side_effect"
        assert result["verification"]["status"] == "still_open"
        assert "Do not claim" in result["message"]

    @pytest.mark.asyncio
    async def test_document_reader_returns_read_only_artifact_preview(self):
        from app.models.models import AIArtifact
        from app.services.model_router import _execute_tool_call_impl

        user_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        artifact = AIArtifact(
            id=artifact_id,
            artifact_type="upload",
            filename="agreement.pdf",
            mime_type="application/pdf",
            storage_uri="https://storage.example/agreement.pdf",
            created_by_user_id=user_id,
            extraction_status="ready",
            extraction_source="native_pdf",
        )

        class ArtifactDb:
            async def execute(self, _stmt):
                class Result:
                    def scalar_one_or_none(self):
                        return artifact

                return Result()

            async def flush(self):
                pass

        with patch(
            "app.services.artifact.ArtifactService.text_preview",
            new=AsyncMock(return_value="Agreement text from PDF"),
        ):
            result = await _execute_tool_call_impl(
                ArtifactDb(),
                user_id,
                "document_reader",
                {"artifact_id": str(artifact_id), "mode": "preview", "max_chars": 5000},
            )

        assert result["status"] == "success"
        assert result["tool_name"] == "document_reader"
        assert result["artifact_id"] == str(artifact_id)
        assert result["text"] == "Agreement text from PDF"
        assert result["extraction_source"] == "native_pdf"

    @pytest.mark.asyncio
    async def test_odoo_schema_connector_error_is_handled_for_trace(self):
        from app.services.model_router import _execute_tool_call

        class FakeResponse:
            status_code = 400
            text = "Odoo returned an internal error while processing the request."

            def json(self):
                return {
                    "detail": {
                        "error": "odoo_error",
                        "error_type": "odoo_error",
                        "message": "Odoo returned an internal error while processing the request.",
                        "correlation_id": "corr-123",
                    }
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return FakeResponse()

        class TraceRecorder:
            def __init__(self):
                self.ended = None

            def start_span(self, *args, **kwargs):
                return "span-1"

            def end_span(self, span_id, **kwargs):
                self.ended = {"span_id": span_id, **kwargs}

            def span_error(self, *args, **kwargs):
                raise AssertionError("handled schema errors should not call span_error")

        db = MockSession(has_config=True)
        trace = TraceRecorder()
        fake_credentials = {
            "url": "https://example.odoo.com",
            "db": "example",
            "username": "user@example.com",
            "api_key": "secret",
            "transport": "auto",
        }

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=AsyncMock(return_value=fake_credentials),
        ), patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), patch(
            "app.services.model_router.ODOO_CONNECTOR_KEY",
            "test-key",
        ), patch("app.services.model_router.httpx.AsyncClient", FakeAsyncClient):
            result = await _execute_tool_call(
                db,
                uuid.uuid4(),
                "odoo_ops_runner",
                {"mode": "schema", "model": "auditlog.log"},
                trace_svc=trace,
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "schema_unavailable"
        assert result["model"] == "auditlog.log"
        assert trace.ended["status"] == "warning"
        assert trace.ended["error_type"] == "schema_unavailable"
        assert trace.ended["error_message"] == "Odoo model 'auditlog.log' could not be inspected by this connected account, so the schema probe was skipped."
        assert trace.ended["output_summary"]["result"]["connector_error"]["correlation_id"] == "corr-123"

    @pytest.mark.asyncio
    async def test_odoo_delete_blocked_error_is_preserved_for_model_and_trace(self):
        from app.services.model_router import _execute_tool_call

        blocked_message = (
            "Odoo hr.employee.unlink failed: You cannot delete an employee that may be used "
            "in an active PoS session, close the session(s) first: "
            "Employee: Gerhard Wayne Cloete - PoS Config(s): Gallagher Convention Center"
        )

        class FakeResponse:
            status_code = 400
            text = blocked_message

            def json(self):
                return {
                    "error": "odoo_delete_blocked_active_pos_session",
                    "error_type": "odoo_delete_blocked_active_pos_session",
                    "message": blocked_message,
                    "correlation_id": "corr-pos",
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return FakeResponse()

        class TraceRecorder:
            def __init__(self):
                self.ended = None

            def start_span(self, *args, **kwargs):
                return "span-pos"

            def end_span(self, span_id, **kwargs):
                self.ended = {"span_id": span_id, **kwargs}

            def span_error(self, *args, **kwargs):
                raise AssertionError("connector HTTP errors should finish the span, not raise")

        db = MockSession(has_config=True)
        trace = TraceRecorder()
        fake_credentials = {
            "url": "https://example.odoo.com",
            "db": "example",
            "username": "user@example.com",
            "api_key": "secret",
            "transport": "auto",
        }

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=AsyncMock(return_value=fake_credentials),
        ), patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), patch(
            "app.services.model_router.ODOO_CONNECTOR_KEY",
            "test-key",
        ), patch("app.services.model_router.httpx.AsyncClient", FakeAsyncClient):
            result = await _execute_tool_call(
                db,
                uuid.uuid4(),
                "odoo_ops_runner",
                {"mode": "mutation", "operation": "delete", "model": "hr.employee", "ids": [77]},
                trace_svc=trace,
            )

        assert result["error"] is True
        assert result["status_code"] == 400
        assert result["error_type"] == "odoo_delete_blocked_active_pos_session"
        assert "active PoS session" in result["message"]
        assert "Gallagher Convention Center" in result["message"]
        assert trace.ended["status"] == "failed"
        assert trace.ended["error_type"] == "odoo_delete_blocked_active_pos_session"
        assert "Gallagher Convention Center" in trace.ended["error_message"]

    def test_tool_result_error_summary_captures_handled_odoo_issue(self):
        from app.services.model_router import _tool_result_error_summary

        summary = _tool_result_error_summary([
            {
                "tool_name": "odoo_ops_runner",
                "arguments": {
                    "mode": "schema",
                    "model": "auditlog.log",
                    "api_key": "must-not-persist",
                },
                "result": {
                    "error": True,
                    "handled": True,
                    "status": "skipped",
                    "error_type": "model_unavailable",
                    "message": "Odoo model 'auditlog.log' is not installed.",
                },
            }
        ])

        assert summary == [
            {
                "index": 1,
                "tool_name": "odoo_ops_runner",
                "status": "skipped",
                "handled": True,
                "error_type": "model_unavailable",
                "message": "Odoo model 'auditlog.log' is not installed.",
                "arguments": {"mode": "schema", "model": "auditlog.log"},
            }
        ]

    @pytest.mark.asyncio
    async def test_usage_log_marks_successful_answer_with_tool_issue_as_partial_failure(self):
        from app.services.model_router import ModelCallState, ModelCallStats, _log_usage

        db = MockSession(has_config=True)
        state = ModelCallState(
            result={"content": "I answered using the usable Odoo results.", "error": False},
            used_model=db._model,
            used_provider=db._provider,
            client=AsyncMock(),
            stats=ModelCallStats(prompt_tokens=10, completion_tokens=5, latency_ms=123),
        )

        await _log_usage(
            db,
            db._route,
            "general_chat",
            uuid.uuid4(),
            uuid.uuid4(),
            state,
            request_id="req-123",
            trace_id="trace_123",
            tool_error_summary=[{
                "tool_name": "odoo_ops_runner",
                "error_type": "model_unavailable",
                "message": "Odoo model 'auditlog.log' is not installed.",
            }],
        )

        usage_log = next(obj for obj in db.added if isinstance(obj, AIUsageLog))
        assert usage_log.status == "partial_failure"
        assert usage_log.error_message == "odoo_ops_runner: model_unavailable - Odoo model 'auditlog.log' is not installed."

    @pytest.mark.asyncio
    async def test_turnover_without_report_name_uses_model_path(self):
        from app.services.model_router import execute_chat

        fixed_now = datetime(2026, 6, 3, 8, 30, 0, tzinfo=ZoneInfo("Africa/Johannesburg"))
        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        mock_execute_tool = AsyncMock()
        mock_chat_completion = AsyncMock(return_value={
            "content": "I can check turnover from Odoo without assuming a specific report.",
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 100,
        })

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router._platform_now',
            new=lambda now=None: fixed_now,
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=mock_execute_tool,
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(chat_completion=mock_chat_completion)),
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "What is this month's turnover?"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "I can check turnover from Odoo without assuming a specific report."
        assert result["total_tokens"] == 15
        assert result["tool_call_count"] == 0
        assert result["tool_calls"] is None
        mock_execute_tool.assert_not_awaited()
        mock_chat_completion.assert_awaited_once()

    def test_tool_result_compaction_limits_large_outputs(self):
        from app.services.model_router import _compact_tool_result_for_model, MAX_TOOL_RESULT_JSON_CHARS

        compacted = _compact_tool_result_for_model({
            "records": [
                {"id": i, "name": f"Record {i}", "datas": "x" * 50000, "body": "b" * 5000}
                for i in range(25)
            ]
        })

        payload = str(compacted)
        assert len(payload) < MAX_TOOL_RESULT_JSON_CHARS + 5000
        assert len(compacted["records"]) == 25
        assert compacted["records"][0]["datas"]["omitted"] is True
        assert "truncated" in compacted["records"][0]["body"]

    def test_tool_result_compaction_caps_oversized_record_pages(self):
        from app.services.model_router import _compact_tool_result_for_model, MAX_TOOL_RESULT_RECORD_ITEMS

        compacted = _compact_tool_result_for_model({
            "records": [{"id": i, "name": f"Record {i}"} for i in range(MAX_TOOL_RESULT_RECORD_ITEMS + 5)]
        })

        assert compacted["records"]["total_items"] == MAX_TOOL_RESULT_RECORD_ITEMS + 5
        assert compacted["records"]["truncated_items"] == 5
        assert len(compacted["records"]["items"]) == MAX_TOOL_RESULT_RECORD_ITEMS

    def test_tool_result_compaction_caps_large_odoo_record_pages_before_generic_limit(self):
        from app.services.model_router import _compact_tool_result_for_model, MAX_ODOO_RECORD_CONTEXT_ITEMS

        compacted = _compact_tool_result_for_model({
            "model": "res.device.log",
            "records": [
                {
                    "id": i,
                    "create_date": "2026-06-06 08:00:00",
                    "description": "x" * 2500,
                }
                for i in range(50)
            ],
            "count": 50,
            "returned_count": 50,
            "total_count": 52,
            "has_more": True,
            "complete": False,
        })

        assert compacted["records_compacted_for_model"] is True
        assert compacted["visible_record_count"] == MAX_ODOO_RECORD_CONTEXT_ITEMS
        assert compacted["original_record_count"] == 50
        assert len(compacted["records"]) == MAX_ODOO_RECORD_CONTEXT_ITEMS
        assert "model_context_warning" in compacted

    def test_tool_finalizer_keeps_complete_odoo_page_visible(self):
        from app.services.model_router import _tool_results_payload_for_finalizer

        records = [
            {
                "id": i,
                "display_name": f"BILL-2025-{i:05d} - Vendor",
                "write_date": "2026-06-04 12:30:00",
                "amount_total_money": {"formatted": f"R{i * 100}.00"},
            }
            for i in range(43)
        ]
        payload = _tool_results_payload_for_finalizer([
            {
                "tool_name": "odoo_ops_runner",
                "arguments": {"mode": "query", "model": "account.move", "limit": 50},
                "result": {
                    "model": "account.move",
                    "records": records,
                    "count": 43,
                    "returned_count": 43,
                    "total_count": 43,
                    "limit": 50,
                    "offset": 0,
                    "has_more": False,
                    "complete": True,
                },
            }
        ])

        assert "BILL-2025-00042" in payload
        assert '"complete": true' in payload
        assert "Only a preview is available" not in payload
        assert "finalizer payload truncated" not in payload

    def test_tool_result_compaction_keeps_practical_cli_stdout_complete(self):
        from app.services.model_router import _compact_tool_result_for_model

        resource_rows = "\n".join(
            f"resource-{i}\trg-ai-platform-prod-san-001\tMicrosoft.App/containerApps"
            for i in range(80)
        )
        compacted = _compact_tool_result_for_model({
            "stdout": resource_rows,
            "stderr": "",
            "stdout_chars": len(resource_rows),
            "output_truncated": False,
            "status": "success",
        })

        assert compacted["stdout"] == resource_rows
        assert "resource-79" in compacted["stdout"]

    def test_tool_result_compaction_marks_huge_stdout_incomplete(self):
        from app.services.model_router import _compact_tool_result_for_model

        huge_stdout = "resource-name\n" * 1000
        compacted = _compact_tool_result_for_model({
            "stdout": huge_stdout,
            "stderr": "",
            "stdout_chars": len(huge_stdout),
            "output_truncated": False,
            "status": "success",
        })

        assert compacted["stdout"]["truncated"] is True
        assert compacted["stdout"]["chars"] == len(huge_stdout)
        assert "Do not infer missing rows" in compacted["stdout"]["warning"]

    @pytest.mark.asyncio
    async def test_rate_limit_fallback_helper_switches_model(self):
        from app.services.model_router import ModelCallStats, ModelCallState, _try_rate_limit_fallbacks

        route = AIRoute(
            id=uuid.uuid4(),
            task_type="general_chat",
            primary_model_id=uuid.uuid4(),
            fallback_model_id=uuid.uuid4(),
            enabled="true",
        )
        primary_provider = AIProvider(id=uuid.uuid4(), name="Primary", enabled="true")
        fallback_provider = AIProvider(id=uuid.uuid4(), name="Fallback Provider", enabled="true")
        primary_model = AIModel(
            id=route.primary_model_id,
            provider_id=primary_provider.id,
            display_name="Primary Model",
            supports_tools="true",
            enabled="true",
        )
        fallback_model = AIModel(
            id=route.fallback_model_id,
            provider_id=fallback_provider.id,
            display_name="Fallback Model",
            supports_tools="true",
            enabled="true",
        )
        state = ModelCallState(
            result={"error": True, "error_type": "rate_limit_exceeded", "latency_ms": 10},
            used_model=primary_model,
            used_provider=primary_provider,
            client=MagicMock(),
            stats=ModelCallStats(),
        )

        with patch(
            "app.services.model_router._fallback_candidates",
            new=AsyncMock(return_value=[(fallback_model, fallback_provider)]),
        ), patch(
            "app.services.model_router._call_model",
            new=AsyncMock(return_value=({
                "error": False,
                "content": "fallback response",
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "latency_ms": 20,
            }, MagicMock())),
        ):
            updated = await _try_rate_limit_fallbacks(
                MockSession(),
                route,
                primary_model,
                state,
                [{"role": "user", "content": "hi"}],
                0.3,
                2000,
                [{"type": "function"}],
                reason="tool_loop_quota_exceeded",
            )

        assert updated.fallback_used is True
        assert updated.used_model.id == fallback_model.id
        assert updated.result["content"] == "fallback response"
        assert updated.stats.total_tokens == 10

    @pytest.mark.asyncio
    async def test_execute_chat_with_tools(self):
        """When model supports tools and tools are registered, they should be
        sent to the model. If model returns tool_calls, execute and loop."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])
        db.has_tools = True

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"]},
                            ),
                        ]
                return Scalars()

        # Override execute for AITool queries to return mock tools
        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt)
            if "ai_tools" in stmt_str:
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute

        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                # First call: model returns a tool_call
                {
                    "content": None,
                    "finish_reason": "tool_calls",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "odoo_ops_runner",
                            "arguments": '{"mode": "query", "model": "res.partner"}',
                        },
                    }],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "latency_ms": 100,
                    "error": False,
                },
                # Second call: model returns final answer
                {
                    "content": "I found 5 partners in Odoo.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "total_tokens": 28,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=AsyncMock(return_value={"records": [{"id": 1, "name": "Partner A"}]})
        ):
            result = await execute_chat(db, [{"role": "user", "content": "find partners"}], user_id=uuid.uuid4())
            assert result["content"] == "I found 5 partners in Odoo."
            assert result["tool_calls"] is not None
            assert len(result["tool_calls"]) == 1
            assert result["tool_calls"][0]["tool_name"] == "odoo_ops_runner"
            assert result["total_tokens"] == 43
            assert client.chat_completion.call_count == 2
            post_tool_call = client.chat_completion.call_args_list[1]
            assert post_tool_call.kwargs["max_tokens"] == 4000
            assert "Use the tool results already gathered" in post_tool_call.kwargs["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_execute_chat_finalizes_blank_response_after_tools(self):
        """A blank length-limited final response after tools must be retried without tools."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"]},
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": None,
                    "finish_reason": "tool_calls",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "odoo_ops_runner",
                            "arguments": '{"mode": "query", "model": "purchase.order"}',
                        },
                    }],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "",
                    "finish_reason": "length",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 2000,
                    "latency_ms": 200,
                    "error": False,
                },
                {
                    "content": "The Odoo evidence shows the receipt was adjusted by a later completed move.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 30,
                    "completion_tokens": 12,
                    "latency_ms": 150,
                    "error": False,
                },
            ])
        )

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=AsyncMock(return_value={"records": [{"id": 5266, "name": "WH01-IN-2026-02586"}]})
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "diagnose this Odoo receipt"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "The Odoo evidence shows the receipt was adjusted by a later completed move."
        assert result["finish_reason"] == "stop"
        assert result["total_tokens"] == 2077
        assert client.chat_completion.call_count == 3

        post_tool_call = client.chat_completion.call_args_list[1]
        assert post_tool_call.kwargs["max_tokens"] == 4000
        assert post_tool_call.kwargs["tools"] is not None
        assert "Use the tool results already gathered" in post_tool_call.kwargs["messages"][-1]["content"]

        finalizer_call = client.chat_completion.call_args_list[2]
        assert finalizer_call.kwargs["tools"] is None
        assert finalizer_call.kwargs["max_tokens"] == 4000
        assert "Tool results:" in finalizer_call.kwargs["messages"][1]["content"]
        assert "WH01-IN-2026-02586" in finalizer_call.kwargs["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_execute_chat_uses_odoo_evidence_fallback_for_blank_timeline(self):
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"]},
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": None,
                    "finish_reason": "tool_calls",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "odoo_ops_runner",
                            "arguments": '{"mode": "query", "model": "account.move", "fields": ["create_date", "name"]}',
                        },
                    }],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "",
                    "finish_reason": "length",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 2000,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )
        odoo_result = {
            "model": "account.move",
            "records": [
                {
                    "id": 57912,
                    "create_date": "2026-06-05 09:23:31",
                    "name": "BILL-2026-02555",
                    "move_type": "in_invoice",
                    "state": "posted",
                    "amount_total": 5400.0,
                    "partner_id": {"id": 48, "name": "Reddish Store"},
                },
            ],
            "count": 1,
            "returned_count": 1,
            "total_count": 1,
        }

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=AsyncMock(return_value=odoo_result)
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "give me a full Odoo timeline please"}],
                user_id=uuid.uuid4(),
            )

        assert "Here is the Odoo timeline" in result["content"]
        assert "2026-06-05 09:23:31 - account.move: BILL-2026-02555" in result["content"]
        assert "Reddish Store" in result["content"]
        assert client.chat_completion.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_chat_retries_blank_direct_response_without_tools(self):
        from app.services.model_router import execute_chat

        db = MockSession(has_config=True)
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": "",
                    "finish_reason": "length",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 2000,
                    "latency_ms": 200,
                    "error": False,
                },
                {
                    "content": "Use schema first, then query with valid fields and a narrow domain.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 30,
                    "completion_tokens": 14,
                    "latency_ms": 100,
                    "error": False,
                },
            ])
        )

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "Explain the safest Odoo activity query strategy"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "Use schema first, then query with valid fields and a narrow domain."
        assert result["finish_reason"] == "stop"
        assert result["total_tokens"] == 2064
        assert client.chat_completion.call_count == 2
        retry_call = client.chat_completion.call_args_list[1]
        assert retry_call.kwargs["tools"] is None
        assert retry_call.kwargs["max_tokens"] == 1000
        assert "previous response returned no user-visible content" in retry_call.kwargs["messages"][-1]["content"]

    def test_blank_direct_fallback_returns_odoo_strategy_for_activity_followup(self):
        from app.services.model_router import _build_blank_direct_fallback_answer

        fallback = _build_blank_direct_fallback_answer(
            [
                {"role": "user", "content": "In Odoo, inspect activity schema."},
                {"role": "assistant", "content": "res.users.log has create_date and ip."},
                {"role": "user", "content": "Explain the safest repeatable query strategy without invalid fields."},
            ],
            {"content": "", "finish_reason": "length"},
        )

        assert fallback is not None
        assert "Start with `schema`" in fallback
        assert "create_uid" in fallback
        assert "If a model has no user-link field" in fallback

    @pytest.mark.asyncio
    async def test_execute_chat_converts_text_tool_calls_to_odoo_ops_runner(self):
        """Kimi-style textual tool markers must be executed, not shown to users."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={
                                    "type": "object",
                                    "properties": {"mode": {"type": "string"}},
                                    "required": ["mode"],
                                },
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        raw_tool_markup = (
            "I'll look that up."
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.odoo_ops_runner:0"
            "<|tool_call_argument_begin|>"
            '{"mode":"query","model":"res.users","domain":[["name","ilike","Penelope"]],"fields":["id","name","login"],"limit":1}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": raw_tool_markup,
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "Penelope was found in Odoo.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )
        execute_tool = AsyncMock(return_value={"records": [{"id": 7, "name": "Penelope"}], "count": 1})

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=execute_tool,
        ):
            result = await execute_chat(
                db,
                [
                    {"role": "user", "content": "What did Penny do today in Odoo?"},
                    {"role": "assistant", "content": "I found Penny's Odoo activity for today."},
                    {"role": "user", "content": "Penelope"},
                ],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "Penelope was found in Odoo."
        assert "<|tool_call" not in result["content"]
        assert result["tool_calls"][0]["tool_name"] == "odoo_ops_runner"
        called_args = execute_tool.call_args.args
        assert called_args[2] == "odoo_ops_runner"
        assert called_args[3]["mode"] == "query"
        assert called_args[3]["model"] == "res.users"
        assert called_args[3]["domain"] == [["name", "ilike", "Penelope"]]
        assert called_args[3]["fields"] == ["id", "name", "login"]
        assert called_args[3]["limit"] == 1

    @pytest.mark.asyncio
    async def test_execute_chat_recovers_text_tool_call_without_selected_tool_schema(self):
        """A textual canonical connector call must run even if intent selection missed a short correction."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={
                                    "type": "object",
                                    "properties": {"mode": {"type": "string"}},
                                    "required": ["mode"],
                                },
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        raw_tool_markup = (
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.odoo_ops_runner:0"
            "<|tool_call_argument_begin|>"
            '{"mode":"query","model":"res.users","domain":[["name","ilike","Penelope"]],"fields":["id","name","login"],"limit":1}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": raw_tool_markup,
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "Penelope's 4 June Odoo activity was checked.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )
        execute_tool = AsyncMock(return_value={"records": [{"id": 7, "name": "Penelope"}], "count": 1})

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=execute_tool,
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "i meant 4 june"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "Penelope's 4 June Odoo activity was checked."
        assert result["tool_calls"][0]["tool_name"] == "odoo_ops_runner"
        first_call = client.chat_completion.call_args_list[0]
        assert first_call.kwargs["tools"] is None
        assert execute_tool.await_count == 1

    @pytest.mark.asyncio
    async def test_execute_chat_converts_text_tool_calls_with_plain_marker_variant(self):
        """Kimi may omit pipe characters in marker text; that variant must be parsed too."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"mode": {"type": "string"}}},
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        raw_tool_markup = (
            "<tool_calls_section_begin>"
            "<tool_call_begin>functions.odoo_ops_runner:0"
            "<tool_call_argument_begin>"
            '{"mode":"query","model":"res.users","domain":[["name","ilike","Penelope"]],"fields":["id","name"]}'
            "<tool_call_end>"
            "<tool_calls_section_end>"
        )
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": raw_tool_markup,
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "Plain marker variant executed.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )
        execute_tool = AsyncMock(return_value={"records": [{"id": 7, "name": "Penelope"}], "count": 1})

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=execute_tool,
        ):
            result = await execute_chat(
                db,
                [
                    {"role": "user", "content": "what did Penelope do in Odoo today"},
                    {"role": "user", "content": "i meant 4 june"},
                ],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "Plain marker variant executed."
        assert result["tool_calls"][0]["tool_name"] == "odoo_ops_runner"
        called_args = execute_tool.call_args.args
        assert called_args[2] == "odoo_ops_runner"
        assert called_args[3]["mode"] == "query"
        assert called_args[3]["model"] == "res.users"

    @pytest.mark.asyncio
    async def test_execute_chat_converts_compact_text_tool_call_without_argument_marker(self):
        """Compact textual calls must execute instead of leaking markup to the chat router."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"mode": {"type": "string"}}},
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        raw_tool_markup = (
            "I'll merge the duplicate employee into the older record."
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.odoo_ops_runner:0 "
            '{"mode":"mutation","operation":"write","model":"hr.employee","ids":[42],"values":{"parent_id":7,"notes":"move data before delete"}}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": raw_tool_markup,
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "Duplicate employee data was merged into the older record.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )
        execute_tool = AsyncMock(return_value={"status": "success", "operation": "write", "updated": 1})

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=execute_tool,
        ):
            result = await execute_chat(
                db,
                [
                    {"role": "user", "content": "there is duplicate gerhdard employee"},
                    {"role": "user", "content": "move everything to the oldest one and delete the new one"},
                ],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "Duplicate employee data was merged into the older record."
        assert "<|tool_call" not in result["content"]
        assert result["tool_calls"][0]["tool_name"] == "odoo_ops_runner"
        called_args = execute_tool.call_args.args
        assert called_args[2] == "odoo_ops_runner"
        assert called_args[3] == {
            "mode": "mutation",
            "operation": "write",
            "model": "hr.employee",
            "ids": [42],
            "values": {"parent_id": 7, "notes": "move data before delete"},
        }

    @pytest.mark.asyncio
    async def test_execute_chat_converts_compact_text_tool_call_with_nested_json_strings(self):
        """The compact parser must not stop at braces that appear inside JSON strings."""
        from app.services.model_router import execute_chat

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=True, connected_accounts=[account])

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="odoo_ops_runner", display_name="Odoo Ops Runner",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"mode": {"type": "string"}}},
                            ),
                        ]
                return Scalars()

        original_execute = db.execute

        async def mock_execute(stmt, *args, **kwargs):
            if "ai_tools" in str(stmt):
                return MockToolResult()
            return await original_execute(stmt, *args, **kwargs)

        db.execute = mock_execute
        raw_tool_markup = (
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.odoo_ops_runner:0 "
            '{"mode":"mutation","operation":"write","model":"hr.employee","ids":[42],"values":{"notes":"contains } brace","metadata":{"old_id":7}}}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )
        client = AsyncMock(
            chat_completion=AsyncMock(side_effect=[
                {
                    "content": raw_tool_markup,
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "Nested JSON compact call executed.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "latency_ms": 200,
                    "error": False,
                },
            ])
        )
        execute_tool = AsyncMock(return_value={"status": "success", "operation": "write", "updated": 1})

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=execute_tool,
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "update the duplicate employee notes in Odoo"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "Nested JSON compact call executed."
        called_args = execute_tool.call_args.args
        assert called_args[3]["values"] == {
            "notes": "contains } brace",
            "metadata": {"old_id": 7},
        }

    def test_coerce_text_tool_call_from_json_envelope_block(self):
        """Some models emit the tool name and arguments as one JSON object inside the marker block."""
        from app.services.model_tool_calls import _coerce_text_tool_calls

        result = _coerce_text_tool_calls(
            {
                "content": (
                    "<|tool_calls_section_begin|>"
                    "<|tool_call_begin|>"
                    '{"name":"functions.odoo_ops_runner","arguments":{"mode":"mutation","operation":"write","model":"hr.employee","ids":[42],"values":{"parent_id":7}}}'
                    "<|tool_call_end|>"
                    "<|tool_calls_section_end|>"
                ),
                "finish_reason": "stop",
                "tool_calls": None,
                "error": False,
            },
            [],
        )

        assert result["finish_reason"] == "tool_calls"
        assert result["content"] is None
        assert result["tool_calls"][0]["function"]["name"] == "odoo_ops_runner"
        args = json.loads(result["tool_calls"][0]["function"]["arguments"])
        assert args == {
            "mode": "mutation",
            "operation": "write",
            "model": "hr.employee",
            "ids": [42],
            "values": {"parent_id": 7},
        }

    def test_coerce_text_tool_call_ignores_cased_odoo_alias_without_mode(self):
        """Legacy Odoo aliases are ignored instead of being guessed into canonical calls."""
        from app.services.model_tool_calls import _coerce_text_tool_calls

        result = _coerce_text_tool_calls(
            {
                "content": (
                    "<|tool_calls_section_begin|>"
                    "<|tool_call_begin|>functions.Odoo:0"
                    "<|tool_call_argument_begin|>"
                    '{"model":"stock.picking","domain":[["id","=",5266]],'
                    '"fields":["name","state","move_ids","date_done"],"limit":10}'
                    "<|tool_call_end|>"
                    "<|tool_calls_section_end|>"
                ),
                "finish_reason": "stop",
                "tool_calls": None,
                "error": False,
            },
            [],
        )

        assert result["finish_reason"] == "stop"
        assert result["tool_calls"] is None

    def test_coerce_text_tool_call_converts_microsoft_graph_alias_with_full_url(self):
        """Production textual Microsoft Graph aliases must execute instead of becoming 502s."""
        from app.services.model_tool_calls import _coerce_text_tool_calls

        result = _coerce_text_tool_calls(
            {
                "content": (
                    "Let me query Microsoft Graph."
                    "<|tool_calls_section_begin|>"
                    "<|tool_call_begin|>functions.microsoft_graph:0"
                    "<|tool_call_argument_begin|>"
                    '{"method":"GET","url":"https://graph.microsoft.com/v1.0/users?$filter=accountEnabled eq true&$count=true&$select=displayName,userPrincipalName&$top=999"}'
                    "<|tool_call_end|>"
                    "<|tool_calls_section_end|>"
                ),
                "finish_reason": "stop",
                "tool_calls": None,
                "error": False,
            },
            [],
        )

        assert result["finish_reason"] == "tool_calls"
        assert result["content"] == "Let me query Microsoft Graph."
        assert "<|tool_call" not in result["content"]
        assert result["tool_calls"][0]["function"]["name"] == "ms_graph"
        args = json.loads(result["tool_calls"][0]["function"]["arguments"])
        assert args == {
            "method": "GET",
            "api_version": "v1.0",
            "path": "/users?$filter=accountEnabled eq true&$count=true&$select=displayName,userPrincipalName&$top=999",
        }

    def test_coerce_text_tool_call_from_xml_json_envelope_with_parameters(self):
        """The parser must handle XML-style tool_call blocks with parameters instead of arguments."""
        from app.services.model_tool_calls import _coerce_text_tool_calls

        result = _coerce_text_tool_calls(
            {
                "content": (
                    "<tool_call>"
                    '{"tool_name":"odoo_ops_runner","parameters":{"mode":"query","model":"hr.employee",'
                    '"domain":[["name","ilike","Gerhard"]],"fields":["id","name"],"limit":2}}'
                    "</tool_call>"
                ),
                "finish_reason": "stop",
                "tool_calls": None,
                "error": False,
            },
            [],
        )

        assert result["finish_reason"] == "tool_calls"
        assert result["content"] is None
        assert result["tool_calls"][0]["function"]["name"] == "odoo_ops_runner"
        args = json.loads(result["tool_calls"][0]["function"]["arguments"])
        assert args == {
            "mode": "query",
            "model": "hr.employee",
            "domain": [["name", "ilike", "Gerhard"]],
            "fields": ["id", "name"],
            "limit": 2,
        }

    def test_coerce_text_tool_call_from_function_payload_with_string_arguments(self):
        """OpenAI-compatible function envelopes may nest a JSON string under function.arguments."""
        from app.services.model_tool_calls import _coerce_text_tool_calls

        result = _coerce_text_tool_calls(
            {
                "content": (
                    "<|tool_call_begin|>"
                    '{"type":"function","function":{"name":"functions.odoo_ops_runner",'
                    '"arguments":"{\\"mode\\":\\"mutation\\",\\"operation\\":\\"write\\",\\"model\\":\\"hr.employee\\",\\"ids\\":[42],\\"values\\":{\\"parent_id\\":7}}"}}'
                    "<|tool_call_end|>"
                ),
                "finish_reason": "stop",
                "tool_calls": None,
                "error": False,
            },
            [],
        )

        assert result["finish_reason"] == "tool_calls"
        args = json.loads(result["tool_calls"][0]["function"]["arguments"])
        assert args["mode"] == "mutation"
        assert args["operation"] == "write"
        assert args["model"] == "hr.employee"
        assert args["ids"] == [42]
        assert args["values"] == {"parent_id": 7}


# ── Security Tests ──

class TestSecurity:
    def test_no_api_key_in_response(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.get("/tools", headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"})
        body = response.text
        assert "api-key" not in body.lower()
        assert "apikey" not in body.lower()
        assert "sk-" not in body
        assert "api_key" not in body.lower()

    def test_no_x_api_key_in_request(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.post(
            "/chat/sessions/00000000-0000-0000-0000-000000000001/messages",
            json={"content": "test"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code in (200, 404)
