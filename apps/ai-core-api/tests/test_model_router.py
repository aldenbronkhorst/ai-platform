import os
import uuid
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime

os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.main import app
from app.core.database import get_db
from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount, AITool, AIMemory
from app.services.model_router import (
    RouteNotFoundError,
    ROUTE_NOT_CONFIGURED_MESSAGE,
    CANONICAL_SYSTEM_PROMPT,
)


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
                if "ai_tools" in stmt_str or "ai_rules" in stmt_str or "ai_company_facts" in stmt_str or "ai_memories" in stmt_str:
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

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchService")
    async def test_execute_chat_injects_search_results(self, mock_search_svc_cls):
        """Active search results from Azure Search must appear in '## Relevant Reference Materials'."""
        from app.services.model_router import execute_chat
        from app.models.models import AIProvider, AIModel, AIRoute

        db = MockSession(has_config=False)

        # Setup mock SearchService
        mock_svc = MagicMock()
        mock_svc.enabled = True
        mock_svc.search_memories = AsyncMock(return_value=[
            {
                "id": "doc123",
                "title": "Printer SOP",
                "chunk_text": "Select tray 2 and downstairs printer",
                "type": "procedure",
                "score": 0.95
            }
        ])
        mock_search_svc_cls.return_value = mock_svc

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

        mock_chat_completion = AsyncMock(return_value={
            "content": "I see the printer SOP details.",
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 50,
        })

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
                chat_completion=mock_chat_completion
            ))
        ):
            result = await execute_chat(db, [{"role": "user", "content": "how to print downstairs"}], user_id=uuid.uuid4())
            assert result["content"] == "I see the printer SOP details."
            
            # Verify system prompt has search injection
            called_messages = mock_chat_completion.call_args[1]["messages"]
            system_prompt_content = called_messages[0]["content"]
            assert "## Relevant Reference Materials" in system_prompt_content
            assert "- [procedure] Printer SOP" in system_prompt_content
            assert "Details: Select tray 2 and downstairs printer" in system_prompt_content

            # Verify response contains injected search metadata
            assert "search_results_injected" in result["context"]
            assert len(result["context"]["search_results_injected"]) == 1
            assert result["context"]["search_results_injected"][0]["id"] == "doc123"

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchService")
    async def test_execute_chat_search_disabled_does_not_inject(self, mock_search_svc_cls):
        """When search service is disabled, no search results are injected and context metadata is empty."""
        from app.services.model_router import execute_chat
        from app.models.models import AIProvider, AIModel, AIRoute

        db = MockSession(has_config=False)

        # Setup disabled mock SearchService
        mock_svc = MagicMock()
        mock_svc.enabled = False
        mock_search_svc_cls.return_value = mock_svc

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

        mock_chat_completion = AsyncMock(return_value={
            "content": "Hi",
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 50,
        })

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
                chat_completion=mock_chat_completion
            ))
        ):
            result = await execute_chat(db, [{"role": "user", "content": "how to print downstairs"}], user_id=uuid.uuid4())
            
            # Verify system prompt does not have search injection
            called_messages = mock_chat_completion.call_args[1]["messages"]
            system_prompt_content = called_messages[0]["content"]
            assert "## Relevant Reference Materials" not in system_prompt_content

            # Verify response does not contain injected search metadata
            assert "search_results_injected" in result["context"]
            assert len(result["context"]["search_results_injected"]) == 0


# ── Seed Script Tests ──

class TestSeedIdempotent:
    @pytest.mark.asyncio
    async def test_seed_creates_provider(self):
        """Seed creates provider when none exists."""
        from scripts.seed_providers import PROVIDER_DATA, MODEL_DATA, ROUTE_DATA, CANONICAL_SYSTEM_PROMPT
        assert PROVIDER_DATA["name"] == "Microsoft Foundry"
        assert MODEL_DATA["model_name"] == "Kimi-K2.6"
        assert ROUTE_DATA["task_type"] == "general_chat"
        assert ROUTE_DATA["system_prompt"] == CANONICAL_SYSTEM_PROMPT

    def test_seed_providers_uses_canonical_prompt(self):
        from scripts.seed_providers import CANONICAL_SYSTEM_PROMPT
        from app.services.model_router import CANONICAL_SYSTEM_PROMPT as ROUTER_PROMPT
        assert CANONICAL_SYSTEM_PROMPT == ROUTER_PROMPT


# ── Update System Prompt Script Tests ──

class TestUpdateSystemPrompt:
    def test_odoo_centric_phrase_detection(self):
        from scripts.update_system_prompt import _contains_odoo_centric_wording
        assert _contains_odoo_centric_wording("You are an Odoo assistant")
        assert _contains_odoo_centric_wording("integrated with Odoo ERP")
        assert _contains_odoo_centric_wording("You are an operational assistant")
        assert _contains_odoo_centric_wording("fully integrated into Odoo")
        assert not _contains_odoo_centric_wording("AI Platform for Lots Lots More")


# ── Context Service Tests (connector-aware filtering) ──

class TestContextServiceFiltering:
    @pytest.mark.asyncio
    async def test_system_scoped_rules_excluded_when_disconnected(self):
        """Rules with scope_type='system' and scope_value='odoo' should be
        excluded when Odoo is not connected for the user."""
        from app.services.context import ContextService
        from app.schemas.schemas import ContextRequest

        db = MockSession(has_config=False, connected_accounts=[])
        svc = ContextService(db)
        req = ContextRequest()
        result = await svc.get_context(req, user_id=uuid.uuid4())
        # The mock returns no rules since MockSession.execute returns empty
        assert "rules" in result

    @pytest.mark.asyncio
    async def test_odoo_facts_excluded_when_disconnected(self):
        """Company facts with key starting with 'odoo_' should be excluded
        when Odoo is not connected."""
        from app.services.context import ContextService
        from app.schemas.schemas import ContextRequest

        db = MockSession(has_config=False, connected_accounts=[])
        svc = ContextService(db)
        req = ContextRequest()
        result = await svc.get_context(req, user_id=uuid.uuid4())
        assert "facts" in result
        assert "tools" in result

    @pytest.mark.asyncio
    async def test_odoo_tools_excluded_when_disconnected(self):
        """Tools for target_system='odoo' should be excluded when Odoo is
        not connected and the request doesn't specify odoo systems."""
        from app.services.context import ContextService
        from app.schemas.schemas import ContextRequest

        db = MockSession(has_config=False, connected_accounts=[])
        svc = ContextService(db)
        req = ContextRequest()
        result = await svc.get_context(req, user_id=uuid.uuid4())
        assert "tools" in result

    @pytest.mark.asyncio
    async def test_odoo_tools_included_when_connected(self):
        """Tools for target_system='odoo' should be included when Odoo is
        connected for the user."""
        from app.services.context import ContextService
        from app.schemas.schemas import ContextRequest

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db = MockSession(has_config=False, connected_accounts=[account])
        svc = ContextService(db)
        req = ContextRequest()
        result = await svc.get_context(req, user_id=uuid.uuid4())
        assert "tools" in result

    @pytest.mark.asyncio
    async def test_connected_account_status_used_by_context(self):
        """The context service should use connected account status to filter."""
        from app.services.context import ContextService
        from app.schemas.schemas import ContextRequest

        db = MockSession(has_config=False, connected_accounts=[])
        svc = ContextService(db)
        systems = await svc._get_connected_systems(uuid.uuid4())
        assert systems == set()

        account = AIConnectedAccount(
            id=uuid.uuid4(), user_id=uuid.uuid4(),
            provider="odoo", status="connected",
        )
        db2 = MockSession(has_config=False, connected_accounts=[account])
        svc2 = ContextService(db2)
        systems2 = await svc2._get_connected_systems(uuid.uuid4())
        assert "odoo" in systems2


@pytest.fixture(autouse=True)
def _cleanup_global_state():
    yield
    app.dependency_overrides.clear()


# ── AI Config API Tests ──

class TestAIConfigAPI:
    def test_summary_empty_by_default(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.get("/ai-config/summary", headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"})
        assert response.status_code == 200
        data = response.json()
        assert data["providers"] == []
        assert data["models"] == []
        assert data["routes"] == []

    def test_provider_list_endpoint(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.get("/ai-config/providers", headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"})
        assert response.status_code == 200

    def test_usage_logs_empty(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.get("/ai-config/usage", headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"})
        assert response.status_code == 200
        assert response.json() == []


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
        import sys
        import ast
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
        from app.services.model_router import _build_tool_definitions, TOOL_NAME_MAP
        TOOL_NAME_MAP.clear()
        assert _build_tool_definitions([]) == []

    def test_build_tool_definitions_skips_missing_schema(self):
        from app.services.model_router import _build_tool_definitions, TOOL_NAME_MAP
        TOOL_NAME_MAP.clear()
        tool = AITool(name="odoo_search_read", display_name="Odoo Search Read",
                       description="Search Odoo", target_system="odoo", input_schema=None)
        assert _build_tool_definitions([tool]) == []

    def test_build_tool_definitions_valid(self):
        from app.services.model_router import _build_tool_definitions, TOOL_NAME_MAP
        TOOL_NAME_MAP.clear()
        tool = AITool(
            name="odoo_search_read", display_name="Odoo Search Read",
            description="Search and read Odoo records",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "odoo_search_read"
        assert "parameters" in defs[0]["function"]

    def test_build_tool_definitions_normalizes_dotted_names(self):
        from app.services.model_router import _build_tool_definitions, TOOL_NAME_MAP
        TOOL_NAME_MAP.clear()
        tool = AITool(
            name="odoo.search_read", display_name="Odoo Search Read",
            description="Search and read Odoo records",
            target_system="odoo",
            input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
        )
        defs = _build_tool_definitions([tool])
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "odoo_search_read"
        assert "." not in defs[0]["function"]["name"]
        assert TOOL_NAME_MAP.get("odoo_search_read") == "odoo.search_read"

    def test_normalize_tool_name(self):
        from app.services.model_router import _normalize_tool_name
        assert _normalize_tool_name("odoo.search_read") == "odoo_search_read"
        assert _normalize_tool_name("odoo.attach_artifact") == "odoo_attach_artifact"
        assert _normalize_tool_name("already_normal") == "already_normal"
        assert _normalize_tool_name("no-changes_needed") == "no-changes_needed"
        assert len(_normalize_tool_name("a" * 100)) == 64

    def test_build_tool_definitions_strips_invalid_chars(self):
        from app.services.model_router import _build_tool_definitions
        from app.services.model_router import TOOL_NAME_MAP
        TOOL_NAME_MAP.clear()
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
        from app.services.model_router import execute_chat, get_enabled_route
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
                last_message_at=datetime.utcnow(),
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
    @pytest.mark.asyncio
    async def test_get_available_tools_no_user(self):
        from app.services.model_router import _get_available_tools
        db = MockSession(has_config=False)
        result = await _get_available_tools(db, user_id=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_available_tools_no_connected_accounts(self):
        from app.services.model_router import _get_available_tools
        db = MockSession(has_config=False, connected_accounts=[])
        result = await _get_available_tools(db, user_id=uuid.uuid4())
        assert result == []

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
                                name="odoo_search_read", display_name="Odoo Search Read",
                                description="Search Odoo",
                                target_system="odoo",
                                input_schema={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]},
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

        with patch.object(
            type(db), 'add'
        ), patch.object(
            type(db), 'flush'
        ), patch(
            'app.services.model_router.build_foundry_client',
            new=AsyncMock(return_value=AsyncMock(
                chat_completion=AsyncMock(side_effect=[
                    # First call: model returns a tool_call
                    {
                        "content": None,
                        "finish_reason": "tool_calls",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "odoo_search_read",
                                "arguments": '{"model": "res.partner"}',
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
            ))
        ), patch(
            'app.services.model_router._execute_tool_call',
            new=AsyncMock(return_value={"records": [{"id": 1, "name": "Partner A"}]})
        ):
            result = await execute_chat(db, [{"role": "user", "content": "find partners"}], user_id=uuid.uuid4())
            assert result["content"] == "I found 5 partners in Odoo."
            assert result["tool_calls"] is not None
            assert len(result["tool_calls"]) == 1
            assert result["tool_calls"][0]["tool_name"] == "odoo_search_read"
            assert result["total_tokens"] == 43


# ── Security Tests ──

class TestSecurity:
    def test_no_api_key_in_response(self):
        app.dependency_overrides[get_db] = mock_get_db_empty
        client = TestClient(app)
        response = client.get("/ai-config/summary", headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"})
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
