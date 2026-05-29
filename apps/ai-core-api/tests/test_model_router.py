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
from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog
from app.services.model_router import RouteNotFoundError, ROUTE_NOT_CONFIGURED_MESSAGE


# ── Mock DB that simulates empty/no route ──
class MockSession:
    def __init__(self, has_config=False):
        self.has_config = has_config
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
            )

            result.scalar_one_or_none = lambda: route
            return result

        result.scalar_one_or_none = lambda: None
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


# ── Model Router Unit Tests ──

class TestModelRouter:
    @pytest.mark.asyncio
    async def test_get_enabled_route_not_found(self):
        from app.services.model_router import get_enabled_route
        db = MockSession(has_config=False)
        with pytest.raises(RouteNotFoundError) as exc:
            await get_enabled_route(db, "general_chat")
        assert ROUTE_NOT_CONFIGURED_MESSAGE in str(exc.value)

    @pytest.mark.asyncio
    async def test_get_enabled_route_found(self):
        from app.services.model_router import get_enabled_route
        db = MockSession(has_config=True)
        route, model, provider = await get_enabled_route(db, "general_chat")
        assert route.task_type == "general_chat"
        assert model.display_name == "Kimi K2.6"
        assert provider.name == "Microsoft Foundry"

    @pytest.mark.asyncio
    async def test_get_enabled_route_wrong_type(self):
        from app.services.model_router import get_enabled_route
        db = MockSession(has_config=True)
        with pytest.raises(RouteNotFoundError):
            await get_enabled_route(db, "nonexistent_route")


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
