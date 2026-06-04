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


class TestChatResponseGuards:
    def test_unprocessed_textual_tool_call_is_rejected(self):
        import uuid
        from fastapi import HTTPException
        from app.routers.chat import _raise_on_blank_response

        with pytest.raises(HTTPException) as exc_info:
            _raise_on_blank_response(
                {
                    "content": "Let me check.<|tool_calls_section_begin|><|tool_call_begin|>functions.odoo:0",
                    "tool_calls": None,
                },
                "req-test",
                uuid.uuid4(),
                uuid.uuid4(),
            )

        assert exc_info.value.detail["error_type"] == "unprocessed_tool_call"

    def test_invalid_assistant_messages_are_excluded_from_model_history(self):
        import uuid
        from app.models.models import AIChatMessage
        from app.routers.chat import _is_valid_history_message

        session_id = uuid.uuid4()
        user_id = uuid.uuid4()

        assert _is_valid_history_message(AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="Useful response",
        ))
        assert not _is_valid_history_message(AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="",
            metadata_json={"failed": True},
        ))
        assert not _is_valid_history_message(AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="<|tool_calls_section_begin|><|tool_call_begin|>functions.odoo:0",
        ))


class TestChatTitleOwnership:
    def test_manual_title_prevents_auto_title(self):
        import uuid
        from app.models.models import AIChatSession
        from app.routers.chat import _can_auto_title_session, _set_session_title_source

        session = AIChatSession(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            title="New Chat",
            status="active",
        )
        assert _can_auto_title_session(session)

        session.title = "Operations Review"
        _set_session_title_source(session, "manual")

        assert session.metadata_json["title_source"] == "manual"
        assert not _can_auto_title_session(session)
