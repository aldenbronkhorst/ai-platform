import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

from app.models.models import AIMemoryUsageEvent
from tests.test_model_router import MockSession


class TestMemoryFeedbackAndTracking:
    @pytest.mark.asyncio
    @patch("app.services.model_router.execute_chat")
    async def test_chat_tracking_on_message(self, mock_execute_chat):
        # Verify memory usage event is recorded when chat message processes
        from app.routers.chat import _process_chat_turn, ChatMessageCreate
        from app.models.models import AIChatSession

        db = MockSession(has_config=False)
        db.refresh = AsyncMock()

        session_id = uuid4()
        user_uuid = uuid4()

        session = AIChatSession(
            id=session_id,
            user_id=str(user_uuid),
            title="Testing feedback",
            status="active"
        )

        # Mock db.execute query dispatches
        class ChatQueryResult:
            def scalar_one_or_none(self):
                return session
            @property
            def scalars(self):
                return lambda: MagicMock(
                    first=lambda: None,
                    all=lambda: []
                )

        db.execute = AsyncMock(return_value=ChatQueryResult())
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        # Mock the chat response containing memories_injected
        mem_id = uuid4()
        mock_execute_chat.return_value = {
            "content": "Use Printer-01 for printing downstairs.",
            "model_provider": "Kimi",
            "model_name": "Kimi K2.6",
            "latency_ms": 100,
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "total_tokens": 10,
            "context": {
                "memories_injected": [
                    {"id": str(mem_id), "title": "Downstairs Printer", "type": "procedure"}
                ]
            }
        }

        req = ChatMessageCreate(content="how to print?")

        msg_res = await _process_chat_turn(db, session_id, req, str(uuid4()), user_uuid)

        assert msg_res.content == "Use Printer-01 for printing downstairs."

        added_items = [arg[0] for arg, kw in db.add.call_args_list]
        # Usage event recorded
        usage_evt = [x for x in added_items if isinstance(x, AIMemoryUsageEvent)][0]
        assert usage_evt.memory_id == mem_id
        assert usage_evt.chat_session_id == session_id
