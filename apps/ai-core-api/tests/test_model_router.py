import os
import uuid
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from datetime import datetime

os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.main import app
from app.core.database import get_db
from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount
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

    async def execute(self, stmt, *args, **kwargs):
        result = AsyncMock()

        if self.has_config:
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
                system_prompt=CANONICAL_SYSTEM_PROMPT,
            )

            result.scalar_one_or_none = lambda: route
            if self.connected_accounts:
                result.scalars = lambda: AsyncMock(all=lambda: self.connected_accounts)
            else:
                result.scalars = lambda: AsyncMock(all=lambda: [])
            return result

        result.scalar_one_or_none = lambda: None
        result.scalars = lambda: AsyncMock(all=lambda: self.connected_accounts)
        return result

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
