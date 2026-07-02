import os
import uuid
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

# Keep connector settings local; auth itself uses APP_ENV=test from conftest.
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


class TestArtifactDownloadSurface:
    """Verifies that public artifact download URLs are no longer exposed."""

    def test_download_artifact_is_not_public_api(self):
        import uuid
        response = client.get(
            f"/artifacts/{uuid.uuid4()}/download",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404


class TestChatResponseGuards:
    def test_recent_verified_tool_facts_compacts_previous_tool_results(self):
        from app.routers.chat import _recent_verified_tool_facts

        facts = _recent_verified_tool_facts([
            SimpleNamespace(
                tool_call_json=[
                    {
                        "tool_name": "odoo",
                        "arguments": {"model": "account.move"},
                        "result": {"model": "account.move", "result": [{"id": 56137, "name": "INV-2026-02128"}]},
                    },
                    {
                        "tool_name": "odoo",
                        "arguments": {"model": "account.partial.reconcile"},
                        "result": {"model": "account.partial.reconcile", "result": [{"id": 47647, "amount": 5206.75}]},
                    },
                    {
                        "tool_name": "workspace",
                        "arguments": {
                            "purpose": "Lookup an Odoo system total",
                        },
                        "result": {
                            "status": "success",
                            "connector_calls": {"odoo": 1},
                            "stdout": "system total: 42",
                        },
                    },
                ]
            )
        ])

        assert "Recent verified tool results from this chat" in facts
        assert "immediately previous assistant reply" in facts
        assert "how it was produced" in facts
        assert "workspace purpose=Lookup an Odoo system total" in facts
        assert "connector_calls=odoo:1" in facts
        assert "system total: 42" in facts
        assert "account.move id=56137" not in facts
        assert "account.partial.reconcile id=47647" not in facts
        assert "records" not in facts

    def test_assistant_metadata_includes_successful_turn_tool_error_summary(self):
        from app.routers.chat import _assistant_metadata

        summary = [{
            "tool_name": "odoo",
            "status": "failed",
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

    def test_assistant_metadata_includes_activity_events_without_raw_reasoning(self):
        from app.routers.chat import _assistant_metadata

        activity_events = [
            {"span_type": "context_build", "event": "span_started"},
            {"span_type": "tool_call", "span_name": "odoo"},
        ]

        metadata = _assistant_metadata(
            {"content": "I answered.", "reasoning_content": "raw provider reasoning"},
            "req-123",
            "trace_123",
            activity_events=activity_events,
        )

        assert metadata["activity_events"] == activity_events
        assert "stream_work_items" not in metadata
        assert "reasoning_content" not in metadata

    def test_assistant_metadata_includes_message_parts(self):
        from app.routers.chat import _assistant_metadata

        message_parts = [
            {"type": "reasoning", "text": "Checking connected data."},
            {
                "type": "tool-call",
                "toolCallId": "tool:1",
                "toolName": "workspace",
                "args": {"language": "python"},
                "argsText": '{"language": "python"}',
                "result": {"stdout": "Done"},
            },
        ]

        metadata = _assistant_metadata(
            {"content": "I answered."},
            "req-123",
            "trace_123",
            message_parts=message_parts,
        )

        assert metadata["message_parts"] == message_parts
        assert "agent_trail" not in metadata
        assert set(metadata) == {"request_id", "trace_id", "message_parts"}

    def test_message_parts_keep_stable_live_stream_shape(self):
        from app.routers.chat import _append_message_text_part, _replace_message_text_part, _upsert_tool_call_part

        parts = []
        _append_message_text_part(parts, "reasoning", "Checking ")
        _append_message_text_part(parts, "reasoning", "Odoo")

        assert [part["type"] for part in parts] == ["reasoning"]
        assert parts[0]["text"] == "Checking Odoo"

        _upsert_tool_call_part(parts, {
            "type": "tool.start",
            "id": "tool:1",
            "name": "workspace",
            "args": {"language": "python"},
            "verboseArgs": '{"language": "python"}',
        })
        _append_message_text_part(parts, "reasoning", "After tool")

        assert [part["type"] for part in parts] == ["reasoning", "tool-call", "reasoning"]
        assert parts[1]["toolCallId"] == "tool:1"
        assert parts[1]["toolName"] == "workspace"
        assert parts[-1]["text"] == "After tool"

        _replace_message_text_part(parts, "reasoning", "Final after tool")

        assert parts[-1]["text"] == "Final after tool"

    def test_final_message_parts_preserve_markdown_line_breaks(self):
        from app.routers.chat import _message_parts_with_final_text

        content = "Summary\n\n---\n\n### GRV table\n\n| GRV | Total |\n| --- | --- |\n| 141814 | R6,614.77 |"

        parts = _message_parts_with_final_text([], content)

        assert parts is not None
        assert parts[-1]["type"] == "text"
        assert parts[-1]["text"] == content
        assert parts[-1]["text"].count("\n") == content.count("\n")

    def test_activity_event_maps_to_agent_tool_event(self):
        from app.routers.chat import _agent_event_from_activity

        event = {
            "event": "span_started",
            "span_id": "span_1",
            "span_type": "tool_call",
            "span_name": "workspace",
            "started_at": "2026-06-30T10:00:00+00:00",
            "input_summary": {
                "tool_name": "workspace",
                "arguments": {"task": "Inspect the account.move fields"},
            },
        }

        agent_event = _agent_event_from_activity(event)

        assert agent_event is not None
        assert agent_event["type"] == "tool.start"
        assert agent_event["id"] == "span_1"
        assert agent_event["name"] == "workspace"
        assert agent_event["context"] == "Run Python: Inspect the account.move fields"
        assert agent_event["startedAt"] == "2026-06-30T10:00:00+00:00"
        assert agent_event["args"] == {"task": "Inspect the account.move fields"}
        assert '"task": "Inspect the account.move fields"' in agent_event["verboseArgs"]
        assert "created_at" in agent_event

    def test_finished_activity_maps_to_structured_tool_payload(self):
        from app.routers.chat import _agent_event_from_activity

        event = {
            "event": "span_finished",
            "span_id": "span_1",
            "span_type": "tool_call",
            "span_name": "workspace",
            "duration_ms": 1250,
            "ended_at": "2026-06-30T10:00:01+00:00",
            "status": "success",
            "input_summary": {
                "tool_name": "workspace",
                "arguments": {"language": "python", "task": "Read P&L report"},
            },
            "output_summary": {
                "result": {
                    "message": "Completed",
                    "stdout": "Revenue: R 5,890,107.02",
                },
            },
        }

        agent_event = _agent_event_from_activity(event)

        assert agent_event is not None
        assert agent_event["type"] == "tool.complete"
        assert agent_event["name"] == "workspace"
        assert agent_event["error"] is False
        assert agent_event["isError"] is False
        assert agent_event["args"] == {"language": "python", "task": "Read P&L report"}
        assert agent_event["result"] == {"message": "Completed", "stdout": "Revenue: R 5,890,107.02"}
        assert agent_event["durationMs"] == 1250
        assert "line" not in agent_event

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
        }])

        assert payload["attachments"] == [{
            "id": attachment_id,
            "filename": "statement.csv",
            "mime_type": "text/csv",
        }]

    def test_content_with_attachment_context_handles_attachment_only_messages(self):
        from app.routers.chat import _content_with_attachment_context

        content = _content_with_attachment_context("", "[Attached file context]\nFile: statement.csv")

        assert content.startswith("Please use the attached file(s).")
        assert "statement.csv" in content

    @pytest.mark.asyncio
    async def test_persist_generated_files_creates_linked_artifacts(self, monkeypatch):
        from app.routers import chat

        created = []

        class FakeArtifactService:
            def __init__(self, db):
                self.db = db

            async def create_from_bytes(self, *, filename, mime_type, content, artifact_type, created_by_user_id):
                created.append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "content": content,
                    "artifact_type": artifact_type,
                    "created_by_user_id": created_by_user_id,
                })
                return SimpleNamespace(id=uuid.uuid4(), filename=filename, mime_type=mime_type)

        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        fake_db = FakeDb()
        session_id = uuid.uuid4()
        user_id = uuid.uuid4()
        assistant_msg = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(chat, "ArtifactService", FakeArtifactService)

        await chat._persist_generated_files(
            fake_db,
            session_id,
            assistant_msg,
            user_id,
            [
                {
                    "filename": "analysis.xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "sha256": "same-file",
                    "content_base64": "ZXhjZWw=",
                },
                {
                    "filename": "analysis.xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "sha256": "same-file",
                    "content_base64": "ZXhjZWw=",
                },
                {
                    "filename": "broken.txt",
                    "mime_type": "text/plain",
                    "content_base64": "not valid base64",
                },
            ],
        )

        assert created == [
            {
                "filename": "analysis.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content": b"excel",
                "artifact_type": "chat-generated",
                "created_by_user_id": user_id,
            }
        ]
        assert len(fake_db.added) == 1
        link = fake_db.added[0]
        assert link.chat_session_id == session_id
        assert link.linked_message_id == assistant_msg.id

    @pytest.mark.asyncio
    async def test_owned_artifacts_rejects_missing_or_foreign_ids(self):
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

    @pytest.mark.asyncio
    async def test_attachment_context_does_not_ocr_pending_pdf_hidden_in_context(self, monkeypatch):
        import uuid
        from app.models.models import AIArtifact
        from app.routers import chat

        class FakeArtifactService:
            def __init__(self, _db):
                pass

            async def text_preview(self, _artifact, max_chars=12_000):
                raise AssertionError("pending PDFs should be read through document_reader, not hidden context OCR")

        monkeypatch.setattr(chat, "ArtifactService", FakeArtifactService)

        artifact = AIArtifact(
            id=uuid.uuid4(),
            artifact_type="chat-upload",
            filename="COSMETIC CONNECTION GRV141814.pdf",
            mime_type="application/pdf",
            storage_uri="https://storage.example/job-files/standalone/grv.pdf",
            extraction_status="pending",
        )

        context = await chat._attachment_context(object(), [artifact])

        assert "COSMETIC CONNECTION GRV141814.pdf" in context
        assert "Use document_reader mode='tables' for tabular documents" in context
        assert "mode='read' for text" in context
        assert "extraction_status=pending" in context

    def test_artifact_manifest_context_exposes_previous_upload_ids(self):
        import uuid
        from app.models.models import AIArtifact
        from app.routers.chat import _artifact_manifest_context

        artifact = AIArtifact(
            id=uuid.uuid4(),
            artifact_type="chat-upload",
            filename="COSMETIC CONNECTION GRV141814.pdf",
            mime_type="application/pdf",
            storage_uri="https://storage.example/chat-uploads/grv.pdf",
            extraction_status="ready",
            extraction_source="azure_document_intelligence:prebuilt-read",
            extracted_text="ocr text",
        )

        context = _artifact_manifest_context([artifact])

        assert "[Available files in this chat]" in context
        assert "COSMETIC CONNECTION GRV141814.pdf" in context
        assert f"id={artifact.id}" in context
        assert "document_reader" in context
        assert "text_chars=8" in context


class TestChatStreaming:
    def test_stream_heartbeat_payload_reports_elapsed_seconds(self):
        from datetime import datetime, timedelta, timezone
        from app.routers.chat import STREAM_HEARTBEAT_SECONDS, _stream_heartbeat_payload

        started_at = datetime.now(timezone.utc) - timedelta(seconds=STREAM_HEARTBEAT_SECONDS + 3)

        payload = _stream_heartbeat_payload("req-heartbeat", started_at)

        assert payload["request_id"] == "req-heartbeat"
        assert payload["elapsed_seconds"] >= STREAM_HEARTBEAT_SECONDS
