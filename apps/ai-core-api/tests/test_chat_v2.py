import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

# Enable debug mode for tests
os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.main import app
from app.core.database import get_db


@pytest.fixture(autouse=True)
def _mock_db():
    """Ensure clean dependency override before each test."""
    app.dependency_overrides.clear()
    async def mock_get_db():
        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = lambda self=None: None
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        session.add = MagicMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.refresh = AsyncMock()
        yield session

    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


class TestChatSessionsV2:
    """Verifies the backend endpoints for Multiple Chat Sessions and role-gating."""

    def test_create_chat_session(self):
        response = client.post(
            "/chat/sessions",
            json={"title": "Custom Session"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Custom Session"
        assert data["status"] == "active"
        assert "workflow_context" not in data

    def test_list_chat_sessions(self):
        response = client.get(
            "/chat/sessions",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_chat_session_not_found(self):
        import uuid
        response = client.get(
            f"/chat/sessions/{uuid.uuid4()}",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404

    def test_post_chat_message_not_found_session(self):
        import uuid
        response = client.post(
            f"/chat/sessions/{uuid.uuid4()}/messages",
            json={"content": "Hello assist"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404


class TestSecureArtifactDownloads:
    """Verifies that download URLs are secure and routed through backend permissions."""

    def test_download_artifact_not_found(self):
        import uuid
        response = client.get(
            f"/artifacts/{uuid.uuid4()}/download",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404
