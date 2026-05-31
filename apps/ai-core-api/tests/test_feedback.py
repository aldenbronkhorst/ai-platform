import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

from app.models.models import AIMemory, AIMemoryUsageEvent, AITask, AIChatMessage, AIUser
from app.schemas.schemas import MemoryFeedbackRequest
from app.routers.memory import record_memory_feedback
from tests.test_model_router import MockSession


class TestMemoryFeedbackAndTracking:
    @pytest.mark.asyncio
    async def test_manual_feedback_worked(self):
        db = MockSession(has_config=False)
        db.refresh = AsyncMock()

        # Create active low confidence memory
        memory = AIMemory(
            id=uuid4(),
            title="Downstairs Printer",
            body="Use Printer-01",
            status="active",
            confidence="low",
            success_count=0,
            failure_count=0,
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(first=lambda: None)
            def scalar_one_or_none(self):
                return memory

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        # Call REST handler directly
        req = MemoryFeedbackRequest(feedback_type="worked", comment="Yes it worked!")
        auth_data = {"user_id": uuid4()}
        updated_mem = await record_memory_feedback(
            memory_id=memory.id,
            req=req,
            db=db,
            auth=auth_data
        )

        assert updated_mem.success_count == 1
        assert updated_mem.confidence == "medium"  # Raised confidence
        assert updated_mem.last_confirmed_at is not None
        assert db.add.call_count >= 1  # Created AIMemoryUsageEvent and logged audit

    @pytest.mark.asyncio
    async def test_manual_feedback_wrong_repeated(self):
        db = MockSession(has_config=False)
        db.refresh = AsyncMock()

        # Create active medium confidence memory with 3 prior failures
        memory = AIMemory(
            id=uuid4(),
            title="Office Passcode",
            body="Code 456",
            status="active",
            confidence="medium",
            success_count=5,
            failure_count=3,
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(first=lambda: None)
            def scalar_one_or_none(self):
                return memory

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        # Submit "wrong" feedback
        req = MemoryFeedbackRequest(feedback_type="wrong", comment="No, it failed.")
        auth_data = {"user_id": uuid4()}
        updated_mem = await record_memory_feedback(
            memory_id=memory.id,
            req=req,
            db=db,
            auth=auth_data
        )

        # failure_count becomes 4 (> 3) -> Status set to needs_review and AITask created
        assert updated_mem.failure_count == 4
        assert updated_mem.confidence == "low"  # Lowered confidence
        assert updated_mem.status == "needs_review"

        added_items = [arg[0] for arg, kw in db.add.call_args_list]
        task_item = [x for x in added_items if isinstance(x, AITask)][0]
        assert task_item.priority == "high"
        assert str(memory.id) in task_item.description

    @pytest.mark.asyncio
    @patch("app.services.model_router.execute_chat")
    async def test_chat_tracking_on_message(self, mock_execute_chat):
        # Verify memory usage event is recorded when chat message processes
        from app.routers.chat import post_chat_message, ChatMessageCreate
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
            "model_provider": "Microsoft Foundry",
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
        request_mock = MagicMock()
        request_mock.headers = {}
        response_mock = MagicMock()

        with patch("app.services.service_bus.send_message_async", new=AsyncMock(return_value=True)):
            msg_res = await post_chat_message(
                session_id=session_id,
                req=req,
                request=request_mock,
                response=response_mock,
                db=db,
                auth={"user_id": user_uuid}
            )

        assert msg_res.content == "Use Printer-01 for printing downstairs."

        added_items = [arg[0] for arg, kw in db.add.call_args_list]
        # Usage event recorded
        usage_evt = [x for x in added_items if isinstance(x, AIMemoryUsageEvent)][0]
        assert usage_evt.memory_id == mem_id
        assert usage_evt.chat_session_id == session_id
