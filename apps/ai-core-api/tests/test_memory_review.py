import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

from app.models.models import AIMemory, AITask
from app.services.memory_review import MemoryReviewService
from tests.test_model_router import MockSession


class TestMemoryReviewService:
    @pytest.mark.asyncio
    @patch("app.services.memory_review.SearchService")
    async def test_review_detects_duplicates(self, mock_search_svc_cls):
        mock_search = MagicMock()
        mock_search.delete_memory_record = AsyncMock(return_value=True)
        mock_search_svc_cls.return_value = mock_search

        db = MockSession(has_config=False)

        # Create two identical active memories
        mem1 = AIMemory(
            id=uuid4(),
            title="Office Wifi Password",
            body="Use Password123",
            status="active",
            type="preference",
            scope_type="global",
            created_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        mem2 = AIMemory(
            id=uuid4(),
            title="Office Wifi Password",
            body="Use Password123-Updated",
            status="active",
            type="preference",
            scope_type="global",
            created_at=datetime.now(timezone.utc)
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [mem1, mem2])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()
        db.flush = AsyncMock()

        svc = MemoryReviewService(db)
        summary = await svc.run_review_job()

        # Verify duplicate was detected and task created
        assert summary["duplicate_tasks_created"] == 1
        assert mem2.status == "needs_review"  # Newer duplicate flagged
        assert db.add.call_count >= 1
        added_task = [args[0] for args, kwargs in db.add.call_args_list if isinstance(args[0], AITask)][0]
        assert isinstance(added_task, AITask)
        assert "WiFi" in added_task.title or "Wifi" in added_task.title
        mock_search.delete_memory_record.assert_called_once_with(mem2.id)

    @pytest.mark.asyncio
    @patch("app.services.memory_review.SearchService")
    async def test_review_detects_conflicts(self, mock_search_svc_cls):
        mock_search = MagicMock()
        mock_search.delete_memory_record = AsyncMock(return_value=True)
        mock_search_svc_cls.return_value = mock_search

        db = MockSession(has_config=False)

        # Create contradictory active memories
        mem1 = AIMemory(
            id=uuid4(),
            title="Printer Policy",
            body="Printers are downstairs",
            status="active",
            type="procedure",
            scope_type="global",
            created_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        mem2 = AIMemory(
            id=uuid4(),
            title="Printer Policy New",
            body="Printers are upstairs",
            status="active",
            type="procedure",
            scope_type="global",
            created_at=datetime.now(timezone.utc)
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [mem1, mem2])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = MemoryReviewService(db)
        summary = await svc.run_review_job()

        assert summary["conflicts_detected"] == 1
        assert mem2.status == "needs_review"  # Newer conflicting memory flagged

    @pytest.mark.asyncio
    @patch("app.services.memory_review.SearchService")
    async def test_review_detects_stale_by_date(self, mock_search_svc_cls):
        mock_search = MagicMock()
        mock_search.delete_memory_record = AsyncMock(return_value=True)
        mock_search_svc_cls.return_value = mock_search

        db = MockSession(has_config=False)

        # Memory expired
        mem = AIMemory(
            id=uuid4(),
            title="Temporary Access Code",
            body="Code 456",
            status="active",
            type="preference",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
            stale_after=datetime.now(timezone.utc) - timedelta(days=1)
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [mem])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = MemoryReviewService(db)
        summary = await svc.run_review_job()

        assert summary["stale_memories_flagged"] == 1
        assert mem.status == "needs_review"

    @pytest.mark.asyncio
    @patch("app.services.memory_review.SearchService")
    async def test_review_detects_low_confidence_failures(self, mock_search_svc_cls):
        mock_search = MagicMock()
        mock_search.delete_memory_record = AsyncMock(return_value=True)
        mock_search_svc_cls.return_value = mock_search

        db = MockSession(has_config=False)

        # High failure count
        mem = AIMemory(
            id=uuid4(),
            title="Odoo login path",
            body="/web/login",
            status="active",
            type="procedure",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
            success_count=1,
            failure_count=4  # failure > 3 and failure > success
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [mem])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = MemoryReviewService(db)
        summary = await svc.run_review_job()

        assert summary["low_confidence_memories_flagged"] == 1
        assert mem.confidence == "low"
        assert mem.status == "needs_review"
