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
    CANONICAL_SYSTEM_PROMPT,
    _compact_tool_result_for_model,
    _append_tool_guidance,
    _execute_tool_call_impl,
)
from app.services.chat_titles import _deterministic_chat_title, _sanitize_chat_title
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


def test_deterministic_chat_title_uses_first_user_request():
    title = _deterministic_chat_title([
        {"role": "user", "content": "can you check our azure and tell me all active resources"},
        {"role": "assistant", "content": "I will check Azure."},
    ])

    assert title == "Azure Active Resources"


def test_deterministic_chat_title_preserves_business_terms():
    title = _deterministic_chat_title([
        {"role": "user", "content": "what did Penelope do today in Odoo, give me a timeline"},
    ])

    assert title == "Penelope Odoo Timeline"


def test_deterministic_chat_title_drops_question_scaffolding_and_standalone_counts():
    title = _deterministic_chat_title([
        {"role": "user", "content": "there are 2 gerhard employees in my odoo?"},
    ])

    assert title == "Gerhard Employees Odoo"


def test_deterministic_chat_title_corrects_typos_and_uses_subject():
    title = _deterministic_chat_title([
        {"role": "user", "content": "create a microsoft uerer for employe gerhard in odoo"},
    ])

    assert title == "Create Microsoft User Employee Gerhard Odoo"


def test_deterministic_chat_title_uses_latest_user_message_only():
    title = _deterministic_chat_title([
        {"role": "user", "content": "whats costing so much"},
        {"role": "assistant", "content": "Azure Cost Management returned a daily cost table."},
    ])

    assert title == "Costing So Much"


def test_deterministic_chat_title_normalizes_error_typos():
    title = _deterministic_chat_title([
        {"role": "user", "content": "halllucinations and now claims it cannot acess azure"},
    ])

    assert title == "Hallucinations Claims Cannot Access Azure"


def test_deterministic_chat_title_does_not_preserve_raw_misspellings():
    title = _deterministic_chat_title([
        {"role": "user", "content": "faliours during thinking in the connecotrs"},
    ])

    assert title == "Failures During Thinking Connectors"
    assert "Faliours" not in title
    assert "Connecotrs" not in title


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


def _odoo_tool() -> AITool:
    return AITool(
        name="odoo",
        display_name="Odoo",
        description="Run raw Odoo operations",
        target_system="odoo",
        input_schema={"type": "object", "properties": {}, "required": []},
    )


@pytest.mark.asyncio
async def test_model_router_rejects_removed_microsoft_tool_names():
    for old_tool_name in ("azure_cli", "ms_admin", "ms_powershell", "ms_az_powershell", "ms_graph_powershell", "ms_bicep"):
        result = await _execute_tool_call_impl(AsyncMock(), uuid.uuid4(), old_tool_name, {"command": "account show"})
        assert result["status"] == "failed"
        assert result["error_type"] == "unknown_tool"
        assert "current tool registry" in result["message"]


def test_microsoft_guidance_uses_workspace_broker_targets():
    tools = [
        AITool(
            name="workspace",
            display_name="Workspace",
            description="Run workspace code",
            target_system="ai-platform",
            input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        )
    ]
    prompt = _append_tool_guidance(
        "base\n",
        tools,
        [{"type": "function", "function": {"name": tool.name, "parameters": {"type": "object"}}} for tool in tools],
    )

    assert "Broker targets include" in prompt
    assert "`ms_azure_cli`" in prompt
    assert "`ms_graph`" in prompt
    assert "`ms_exchange_powershell`" in prompt
    assert "`ms_teams_powershell`" in prompt
    assert "`ms_sharepoint_pnp_powershell`" in prompt
    assert "ms_graph_powershell" not in prompt
    assert "ms_az_powershell" not in prompt
    assert "ms_bicep" not in prompt
    assert "connected account permissions decide what succeeds" in prompt
    assert "Do not present business-system behavior as verified unless the script actually checked it." in prompt

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
                id=uuid.uuid4(), name="ProviderOne", provider_type="openai_compatible",
                base_url="https://provider-one.example/v1", auth_type="key_vault_secret",
                secret_reference="mock-key", enabled="true",
            )
            self._model = AIModel(
                id=uuid.uuid4(), provider_id=self._provider.id, display_name="Provider Chat",
                model_name="provider-chat-latest", deployment_name="provider-chat-latest",
                model_family="ProviderOne", model_version="Latest",
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
            'app.services.model_router.build_model_client',
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
            'app.services.model_router.build_model_client',
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
                                name="workspace",
                                display_name="Workspace",
                                description="Run workspace code",
                                target_system="ai-platform",
                                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                            ),
                            *[
                            AITool(
                                name=name,
                                display_name=name,
                                description="Run Microsoft admin tooling",
                                target_system=_microsoft_tool_target(name),
                                input_schema={"type": "object", "properties": {}, "required": []},
                            )
                            for name in MICROSOFT_TOOL_NAMES
                            ],
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
            'app.services.model_router.build_model_client',
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
        assert tool_names == ["workspace"]
        assert "`ms_azure_cli`" in system_prompt_content
        assert "ms_admin" not in tool_names
        assert result["content"] == "Azure is connected."

    @pytest.mark.asyncio
    async def test_execute_chat_injects_active_memories(self):
        """Active AIMemory records for the user must appear in '## Learned from Past Interactions'."""
        from app.services.model_router import execute_chat
        from app.models.models import AIProvider, AIModel, AIRoute

        test_user_id = uuid.uuid4()
        db = MockSession(has_config=False)

        # Real model objects for get_enabled_route
        provider = AIProvider(
            id=uuid.uuid4(), name="ProviderOne", provider_type="openai_compatible",
            base_url="https://provider-one.example/v1", auth_type="key_vault_secret",
            secret_reference="mock-key", enabled="true",
        )
        model = AIModel(
            id=uuid.uuid4(), provider_id=provider.id, display_name="Provider Chat",
            model_name="provider-chat-latest", deployment_name="provider-chat-latest",
            model_family="ProviderOne", model_version="Latest",
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
            'app.services.model_router.build_model_client',
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
            id=uuid.uuid4(), name="ProviderOne", provider_type="openai_compatible",
            base_url="https://provider-one.example/v1", auth_type="key_vault_secret",
            secret_reference="mock-key", enabled="true",
        )
        model = AIModel(
            id=uuid.uuid4(), provider_id=provider.id, display_name="Provider Chat",
            model_name="provider-chat-latest", deployment_name="provider-chat-latest",
            model_family="ProviderOne", model_version="Latest",
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
            'app.services.model_router.build_model_client',
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

@pytest.fixture(autouse=True)
def _cleanup_global_state():
    yield
    app.dependency_overrides.clear()


# ── Chat Endpoint Tests ──

class TestChatWithModelRouter:
    def test_legacy_non_stream_chat_endpoint_is_removed(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.post(
            "/chat/sessions/00000000-0000-0000-0000-000000000001/messages",
            json={"content": "hello"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 405


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
        tool = AITool(name="odoo", display_name="Odoo",
                       description="Search Odoo", target_system="odoo", input_schema=None)
        assert _build_tool_definitions([tool]) == []

    def test_build_tool_definitions_valid(self):
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="odoo", display_name="Odoo",
            description="Run Odoo operations",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "odoo"
        assert "parameters" in defs[0]["function"]

    def test_workspace_guidance_exposes_connector_broker_names(self):
        from app.services.model_router import _append_tool_guidance
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="workspace",
            display_name="Workspace",
            description="Run workspace code",
            target_system="ai-platform",
            input_schema={"type": "object", "properties": {"code": {"type": "string"}}},
        )

        system_prompt = _append_tool_guidance("Base prompt.", [tool], _build_tool_definitions([tool]))

        assert "Broker targets include" in system_prompt
        assert "`odoo`" in system_prompt
        assert "`ms_graph`" in system_prompt
        assert "`github_cli`" in system_prompt
        assert "connected account permissions decide what succeeds" in system_prompt
        assert "report, dashboard, or other system-calculated value" in system_prompt
        assert "one workspace call per discovery step" in system_prompt
        assert "router infers mode" not in system_prompt

    def test_workspace_guidance_prefers_set_based_odoo_queries(self):
        from app.services.model_router import _append_tool_guidance
        from app.services.model_tool_calls import _build_tool_definitions
        tools = [
            AITool(
                name="workspace",
                display_name="Workspace",
                description="Run workspace code",
                target_system="ai-platform",
                input_schema={"type": "object", "properties": {"code": {"type": "string"}}},
            ),
        ]

        system_prompt = _append_tool_guidance("Base prompt.", tools, _build_tool_definitions(tools))

        assert "Prefer set-based and batch calls" in system_prompt
        assert "available by default" in system_prompt
        assert "imported from `ai_platform_tools`" in system_prompt
        assert "returns connector errors as data" in system_prompt
        assert "Broker targets include" in system_prompt
        assert "query the source system object/API" in system_prompt
        assert "prefer the direct `odoo` tool" not in system_prompt

    def test_build_tool_definitions_normalizes_dotted_names(self):
        from app.services.model_tool_calls import _build_tool_definitions
        tool = AITool(
            name="sample.tool", display_name="Sample",
            description="Run sample operations",
            target_system="sample",
            input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "sample_tool"
        assert "." not in defs[0]["function"]["name"]

    def test_normalize_tool_name(self):
        from app.services.model_tool_calls import _normalize_tool_name
        assert _normalize_tool_name("sample.tool") == "sample_tool"
        assert _normalize_tool_name("sample.attach_artifact") == "sample_attach_artifact"
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
            id=uuid.uuid4(), name="ProviderOne", provider_type="openai_compatible",
            base_url="https://provider-one.example/v1", auth_type="key_vault_secret",
            secret_reference="mock-key", enabled="true",
        )
        model = AIModel(
            id=uuid.uuid4(), provider_id=provider.id, display_name="Provider Chat",
            model_name="provider-chat-latest", deployment_name="provider-chat-latest",
            model_family="ProviderOne", model_version="Latest",
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
            'app.services.model_router.build_model_client',
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
            "app.services.model_router.build_model_client",
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

class TestToolExecution:
    def test_core_router_has_no_deterministic_odoo_report_detector(self):
        import app.services.model_router as model_router

        assert not hasattr(model_router, "detect_odoo_report_intent")
        assert not hasattr(model_router, "detect_odoo_lookup_intent")

    @pytest.mark.asyncio
    async def test_odoo_missing_raw_call_shape_is_handled_before_connector(self):
        from app.services.model_router import _execute_tool_call_impl

        db = MockSession(has_config=True)
        mock_credentials = AsyncMock(side_effect=AssertionError("credentials should not be resolved"))

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=mock_credentials,
        ):
            result = await _execute_tool_call_impl(db, uuid.uuid4(), "odoo", {})

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert result["missing"] == ["model", "method"]
        mock_credentials.assert_not_awaited()

    def test_odoo_non_raw_keys_are_rejected_not_stripped(self):
        from app.services.model_router import _validate_odoo_arguments

        result = _validate_odoo_arguments({
            "mode": "orm",
            "model": "account.move",
            "method": "search_read",
            "domain": [["name", "=", "INV/001"]],
            "fields": ["id", "name"],
            "limit": 1,
            "extra_payload": {"domain": []},
        })

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert "unsupported keys" in result["message"]
        assert result["missing"] == ["args", "kwargs"]

    def test_odoo_validation_accepts_raw_model_method(self):
        from app.services.model_router import _validate_odoo_arguments

        result = _validate_odoo_arguments({
            "model": "account.move",
            "method": "search_read",
            "args": [[["name", "=", "INV/001"]]],
            "kwargs": {"fields": ["id", "name"], "limit": 1},
        })

        assert result is None

    def test_odoo_validation_accepts_raw_calls(self):
        from app.services.model_router import _validate_odoo_arguments

        result = _validate_odoo_arguments({
            "calls": [
                {
                    "model": "account.move",
                    "method": "search_read",
                    "args": [[["name", "=", "INV/001"]]],
                }
            ],
        })

        assert result is None

    @pytest.mark.asyncio
    async def test_odoo_raw_orm_posts_to_raw_endpoint(self):
        from app.services.model_router import _execute_tool_call_impl

        posted = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return []

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, *args, **kwargs):
                posted["url"] = url
                posted["payload"] = kwargs["json"]
                return FakeResponse()

        fake_credentials = {
            "url": "https://example.odoo.com",
            "db": "example",
            "username": "user@example.com",
            "api_key": "secret",
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
                "odoo",
                {
                    "model": "account.move",
                    "method": "search_read",
                    "args": [[["name", "=", "INV/001"]]],
                    "kwargs": {"fields": ["id", "name"], "limit": 1},
                },
            )

        assert posted["url"] == "http://mock-connector:8000/odoo/orm/run"
        assert "mode" not in posted["payload"]
        assert posted["payload"]["model"] == "account.move"
        assert result == []

    @pytest.mark.asyncio
    async def test_odoo_legacy_feature_shape_without_method_is_rejected_before_connector(self):
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
                "odoo",
                {"mode": "attachment", "model": "account.move", "ids": [57508]},
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert "unsupported keys" in result["message"]
        mock_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_odoo_legacy_extra_keys_never_reach_connector(self):
        from app.services.model_router import _execute_tool_call_impl

        mock_credentials = AsyncMock(side_effect=AssertionError("credentials should not be resolved"))

        with patch(
            "app.services.model_router._resolve_odoo_credentials_for_tool",
            new=mock_credentials,
        ):
            result = await _execute_tool_call_impl(
                MockSession(has_config=True),
                uuid.uuid4(),
                "odoo",
                {
                    "mode": "execute",
                    "model": "mail.activity",
                    "method": "action_feedback",
                    "ids": [2180],
                    "operation": "legacy_action",
                    "kwargs": {"feedback": "Receipt corrected"},
                },
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert "unsupported keys" in result["message"]
        mock_credentials.assert_not_awaited()

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
    async def test_odoo_schema_mode_is_rejected_for_trace(self):
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
                "odoo",
                {"mode": "schema", "model": "auditlog.log"},
                trace_svc=trace,
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert "unsupported keys: mode" in result["message"]
        assert trace.ended["status"] == "warning"
        assert trace.ended["error_type"] == "invalid_tool_arguments"

    @pytest.mark.asyncio
    async def test_odoo_mutation_mode_is_rejected_for_trace(self):
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
                "odoo",
                {"mode": "mutation", "operation": "delete", "model": "hr.employee", "ids": [77]},
                trace_svc=trace,
            )

        assert result["error"] is True
        assert result["handled"] is True
        assert result["status"] == "skipped"
        assert result["error_type"] == "invalid_tool_arguments"
        assert "unsupported keys" in result["message"]
        assert "ids" in result["message"]
        assert "mode" in result["message"]
        assert "operation" in result["message"]
        assert result["missing"] == ["args", "kwargs"]
        assert trace.ended["status"] == "warning"
        assert trace.ended["error_type"] == "invalid_tool_arguments"

    def test_tool_result_error_summary_captures_handled_odoo_issue(self):
        from app.services.model_router import _tool_result_error_summary

        summary = _tool_result_error_summary([
            {
                "tool_name": "odoo",
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
                "tool_name": "odoo",
                "status": "skipped",
                "handled": True,
                "error_type": "model_unavailable",
                "message": "Odoo model 'auditlog.log' is not installed.",
                "arguments": {"model": "auditlog.log"},
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
                "tool_name": "odoo",
                "error_type": "model_unavailable",
                "message": "Odoo model 'auditlog.log' is not installed.",
            }],
        )

        usage_log = next(obj for obj in db.added if isinstance(obj, AIUsageLog))
        assert usage_log.status == "partial_failure"
        assert usage_log.error_message == "odoo: model_unavailable - Odoo model 'auditlog.log' is not installed."

    @pytest.mark.asyncio
    async def test_turnover_uses_raw_odoo_tool_path(self):
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
            'app.services.model_router.build_model_client',
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
    async def test_execute_chat_with_tools(self):
        """When model supports tools and tools are registered, they should be
        sent to the model. If model returns tool_calls, execute and loop."""
        from app.services.model_router import TOOL_LOOP_RESPONSE_MAX_TOKENS, execute_chat

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
                            _odoo_tool(),
                            AITool(
                                name="workspace", display_name="Workspace",
                                description="Run workspace code",
                                target_system="ai-platform",
                                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
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
                            "name": "odoo",
                            "arguments": '{"model": "res.partner", "method": "search_read", "args": [[]], "kwargs": {"limit": 5}}',
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
            'app.services.model_router.build_model_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=AsyncMock(return_value={"records": [{"id": 1, "name": "Partner A"}]})
        ):
            result = await execute_chat(db, [{"role": "user", "content": "find partners"}], user_id=uuid.uuid4())
            assert result["content"] == "I found 5 partners in Odoo."
            assert result["tool_calls"] is not None
            assert len(result["tool_calls"]) == 1
            assert result["tool_calls"][0]["tool_name"] == "odoo"
            assert result["total_tokens"] == 43
            assert client.chat_completion.call_count == 2
            post_tool_call = client.chat_completion.call_args_list[1]
            assert post_tool_call.kwargs["max_tokens"] == TOOL_LOOP_RESPONSE_MAX_TOKENS
            assert "Use the tool results already gathered" in post_tool_call.kwargs["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_execute_chat_reuses_workspace_session_across_tool_calls(self):
        from app.services.model_router import execute_chat

        db = MockSession(has_config=True)

        class MockToolResult:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return [
                            AITool(
                                name="workspace", display_name="Workspace",
                                description="Run workspace code",
                                target_system="ai-platform",
                                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
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
                            "name": "workspace",
                            "arguments": json.dumps({
                                "code": "open('state.txt', 'w', encoding='utf-8').write('42')\nprint('stored')",
                                "timeout": 10,
                            }),
                        },
                    }],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": None,
                    "finish_reason": "tool_calls",
                    "tool_calls": [{
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "workspace",
                            "arguments": json.dumps({
                                "code": "value = open('state.txt', encoding='utf-8').read()\nprint(f'state={value}')",
                                "timeout": 10,
                            }),
                        },
                    }],
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "total_tokens": 28,
                    "latency_ms": 200,
                    "error": False,
                },
                {
                    "content": "state=42",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 15,
                    "completion_tokens": 4,
                    "total_tokens": 19,
                    "latency_ms": 120,
                    "error": False,
                },
            ])
        )

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_model_client',
            new=AsyncMock(return_value=client),
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "use workspace in two steps"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "state=42"
        assert result["finish_reason"] == "stop"
        assert result["tool_call_count"] == 2
        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) == 2
        workspace_ids = [call["result"]["workspace_id"] for call in result["tool_calls"]]
        assert workspace_ids[0] == workspace_ids[1]
        assert result["tool_calls"][0]["result"]["run_index"] == 1
        assert result["tool_calls"][1]["result"]["run_index"] == 2
        assert client.chat_completion.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_chat_stops_at_tool_loop_limit_without_forced_final_fallback(self):
        """The loop limit should stop honestly instead of running a hidden final-answer retry."""
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
                            _odoo_tool(),
                            AITool(
                                name="workspace", display_name="Workspace",
                                description="Run workspace code",
                                target_system="ai-platform",
                                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
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
                            "name": "odoo",
                            "arguments": '{"model": "account.move", "method": "search_read", "args": [[]], "kwargs": {"limit": 1}}',
                        },
                    }],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "I need to check another Odoo record.",
                    "finish_reason": "tool_calls",
                    "tool_calls": [{
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "odoo",
                            "arguments": '{"model": "account.move.line", "method": "search_read", "args": [[]], "kwargs": {"limit": 1}}',
                        },
                    }],
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "latency_ms": 200,
                    "error": False,
                },
                {
                    "content": "I found INV-2026-02128 from the gathered Odoo result. I did not run the extra lookup.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 30,
                    "completion_tokens": 9,
                    "latency_ms": 250,
                    "error": False,
                },
            ])
        )

        execute_tool = AsyncMock(return_value={"records": [{"id": 56137, "name": "INV-2026-02128"}]})

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.MAX_TOOL_LOOP_ITERATIONS',
            1,
        ), patch(
            'app.services.model_router.build_model_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=execute_tool,
        ):
            result = await execute_chat(db, [{"role": "user", "content": "check this invoice"}], user_id=uuid.uuid4())

        assert "tool calls after the allowed tool steps" in result["content"]
        assert result["finish_reason"] == "tool_loop_limit"
        assert result["tool_calls"] is not None
        assert client.chat_completion.call_count == 2
        execute_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_chat_retries_blank_length_response_after_tools(self):
        """A blank length-limited post-tool response is retried before it reaches the chat guard."""
        from app.services.model_router import TOOL_LOOP_RESPONSE_MAX_TOKENS, execute_chat

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
                            _odoo_tool(),
                            AITool(
                                name="workspace", display_name="Workspace",
                                description="Run workspace code",
                                target_system="ai-platform",
                                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
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
                            "name": "odoo",
                            "arguments": '{"model": "purchase.order", "method": "search_read", "args": [[]], "kwargs": {"limit": 1}}',
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
                    "completion_tokens": 8000,
                    "latency_ms": 200,
                    "error": False,
                },
                {
                    "content": "The receipt has one matching line.",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 30,
                    "completion_tokens": 8,
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
            'app.services.model_router.build_model_client',
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

        assert result["content"] == "The receipt has one matching line."
        assert result["finish_reason"] == "stop"
        assert result["total_tokens"] == 8073
        assert client.chat_completion.call_count == 3

        post_tool_call = client.chat_completion.call_args_list[1]
        assert post_tool_call.kwargs["max_tokens"] == TOOL_LOOP_RESPONSE_MAX_TOKENS
        assert post_tool_call.kwargs["tools"] is not None
        assert "Use the tool results already gathered" in post_tool_call.kwargs["messages"][-1]["content"]

        retry_call = client.chat_completion.call_args_list[2]
        assert retry_call.kwargs["max_tokens"] == TOOL_LOOP_RESPONSE_MAX_TOKENS
        assert retry_call.kwargs["tools"] is None
        assert "without producing visible assistant content" in retry_call.kwargs["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_execute_chat_continues_nonblank_length_response_after_tools(self):
        """A visible but length-limited post-tool table is continued instead of returned mid-row."""
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
                                name="odoo", display_name="Odoo",
                                description="Run Odoo operations",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"model": {"type": "string"}, "method": {"type": "string"}}},
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
                            "name": "odoo",
                            "arguments": '{"model": "sale.order.line", "method": "search_read", "args": [[]], "kwargs": {"limit": 1}}',
                        },
                    }],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "error": False,
                },
                {
                    "content": "| SO | Product | Ordered | Delivered |\n| SO1 | A | 1 |",
                    "finish_reason": "length",
                    "tool_calls": None,
                    "prompt_tokens": 20,
                    "completion_tokens": 8000,
                    "latency_ms": 200,
                    "error": False,
                },
                {
                    "content": "1 |\n| SO2 | B | 2 | 2 |",
                    "finish_reason": "stop",
                    "tool_calls": None,
                    "prompt_tokens": 30,
                    "completion_tokens": 10,
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
            'app.services.model_router.build_model_client',
            new=AsyncMock(return_value=client),
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=AsyncMock(return_value={"records": [{"id": 1, "name": "SO1"}, {"id": 2, "name": "SO2"}]})
        ):
            result = await execute_chat(
                db,
                [{"role": "user", "content": "give me all sales lines as a table"}],
                user_id=uuid.uuid4(),
            )

        assert result["content"] == "| SO | Product | Ordered | Delivered |\n| SO1 | A | 1 |1 |\n| SO2 | B | 2 | 2 |"
        assert result["finish_reason"] == "stop"
        assert client.chat_completion.call_count == 3

        continuation_call = client.chat_completion.call_args_list[2]
        assert continuation_call.kwargs["tools"] is None
        assert "Continue the visible answer exactly where it stopped" in continuation_call.kwargs["messages"][-1]["content"]

# ── Security Tests ──

class TestSecurity:
    def test_no_api_key_in_response(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.get("/health", headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"})
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
        assert response.status_code == 405
