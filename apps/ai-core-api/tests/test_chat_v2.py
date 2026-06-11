import os
import pytest
from unittest.mock import AsyncMock, MagicMock
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

    def test_legacy_non_stream_chat_post_is_removed(self):
        import uuid
        response = client.post(
            f"/chat/sessions/{uuid.uuid4()}/messages",
            json={"content": "Hello assist"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 405


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
    def test_assistant_metadata_includes_successful_turn_tool_error_summary(self):
        from app.routers.chat import _assistant_metadata

        summary = [{
            "tool_name": "odoo_ops_runner",
            "status": "skipped",
            "handled": True,
            "error_type": "model_unavailable",
            "message": "Odoo model 'auditlog.log' is not installed.",
        }]

        metadata = _assistant_metadata(
            {"content": "I answered.", "tool_error_summary": summary, "has_tool_errors": True},
            "req-123",
            "trace_123",
        )

        assert metadata["request_id"] == "req-123"
        assert metadata["trace_id"] == "trace_123"
        assert metadata["has_tool_errors"] is True
        assert metadata["tool_error_summary"] == summary

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


class TestChatAttachments:
    def test_chat_message_payload_includes_attachments(self):
        import uuid
        from datetime import datetime, timezone
        from app.models.models import AIChatMessage
        from app.routers.chat import _chat_message_payload

        session_id = uuid.uuid4()
        user_id = uuid.uuid4()
        message = AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=session_id,
            user_id=user_id,
            role="user",
            content="Please inspect this.",
            created_at=datetime.now(timezone.utc),
        )
        attachment_id = uuid.uuid4()

        payload = _chat_message_payload(message, [{
            "id": attachment_id,
            "filename": "statement.csv",
            "mime_type": "text/csv",
            "artifact_type": "job-file",
        }])

        assert payload["attachments"] == [{
            "id": attachment_id,
            "filename": "statement.csv",
            "mime_type": "text/csv",
            "artifact_type": "job-file",
        }]

    def test_content_with_attachment_context_handles_attachment_only_messages(self):
        from app.routers.chat import _content_with_attachment_context

        content = _content_with_attachment_context("", "[Attached file context]\nFile: statement.csv")

        assert content.startswith("Please use the attached file(s).")
        assert "statement.csv" in content

    @pytest.mark.asyncio
    async def test_owned_artifacts_rejects_missing_or_foreign_ids(self):
        import uuid
        from fastapi import HTTPException
        from app.routers.chat import _owned_artifacts_for_chat

        class EmptyResult:
            def scalars(self):
                return self

            def all(self):
                return []

        class Db:
            async def execute(self, _stmt):
                return EmptyResult()

        with pytest.raises(HTTPException) as exc_info:
            await _owned_artifacts_for_chat(Db(), uuid.uuid4(), [uuid.uuid4()])

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_type"] == "artifact_not_found"

    @pytest.mark.asyncio
    async def test_attachment_context_uses_text_preview(self, monkeypatch):
        import uuid
        from app.models.models import AIArtifact
        from app.routers import chat

        class FakeArtifactService:
            def __init__(self, _db):
                pass

            async def text_preview(self, _artifact, max_chars=12_000):
                return "uploaded,csv,text"

        monkeypatch.setattr(chat, "ArtifactService", FakeArtifactService)

        artifact = AIArtifact(
            id=uuid.uuid4(),
            artifact_type="job-file",
            filename="statement.csv",
            mime_type="text/csv",
            storage_uri="https://storage.example/job-files/standalone/statement.csv",
        )

        context = await chat._attachment_context(object(), [artifact])

        assert "statement.csv" in context
        assert "uploaded,csv,text" in context
        assert "user-provided content" in context


class TestChatStreaming:
    def test_stream_heartbeat_payload_reports_elapsed_seconds(self):
        from datetime import datetime, timedelta, timezone
        from app.routers.chat import STREAM_HEARTBEAT_SECONDS, _stream_heartbeat_payload

        started_at = datetime.now(timezone.utc) - timedelta(seconds=STREAM_HEARTBEAT_SECONDS + 3)

        payload = _stream_heartbeat_payload("req-heartbeat", started_at)

        assert payload["request_id"] == "req-heartbeat"
        assert payload["elapsed_seconds"] >= STREAM_HEARTBEAT_SECONDS
