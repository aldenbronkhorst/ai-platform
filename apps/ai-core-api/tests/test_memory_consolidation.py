from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock
from uuid import uuid4
import pytest

from app.models.models import AIMemory, AITask, AIRule
from app.services.memory_consolidation import MemoryConsolidationService
from tests.test_model_router import MockSession


class TestMemoryConsolidationService:
    @pytest.mark.asyncio
    async def test_consolidation_resolved_cases_to_sop(self):
        db = MockSession(has_config=False)

        # Create 2 related resolved cases
        mem1 = AIMemory(
            id=uuid4(),
            title="Printer paper jam downstairs",
            body="Pull tray 2 and check paper alignment",
            status="active",
            type="resolved_case",
            scope_type="global",
            created_at=datetime.now(timezone.utc) - timedelta(days=2)
        )
        mem2 = AIMemory(
            id=uuid4(),
            title="SOP: print downstairs check",
            body="downstairs printer setup needs tray 2 loaded",
            status="active",
            type="resolved_case",
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

        svc = MemoryConsolidationService(db)
        stats = await svc.consolidate_memories()

        # Verify SOP and Task were proposed/created
        assert stats["sop_candidates_created"] == 1
        assert stats["merge_tasks_created"] == 1
        assert db.add.call_count >= 2  # One AIMemory (SOP) + One AITask + One AIAuditEvent (mocked by audit logs)

        added_items = [arg[0] for arg, kw in db.add.call_args_list]
        sop_item = [x for x in added_items if isinstance(x, AIMemory)][0]
        task_item = [x for x in added_items if isinstance(x, AITask)][0]

        assert sop_item.type == "procedure"
        assert sop_item.status == "needs_review"  # Not auto-activated
        assert "SOP Candidate: Printer paper jam downstairs" in sop_item.title
        assert str(mem1.id) in sop_item.metadata_json["source_memory_ids"]
        assert isinstance(task_item, AITask)

    @pytest.mark.asyncio
    async def test_consolidation_corrections_to_rule(self):
        db = MockSession(has_config=False)

        # Create 2 currency corrections
        mem1 = AIMemory(
            id=uuid4(),
            title="Use ZAR for bills",
            body="Use R / ZAR consistently for odoo bills, do not use dollars",
            status="active",
            type="correction",
            scope_type="global",
            created_at=datetime.now(timezone.utc) - timedelta(days=5)
        )
        mem2 = AIMemory(
            id=uuid4(),
            title="Currency display rule check",
            body="Do not show $ for company invoices, default is ZAR",
            status="active",
            type="correction",
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

        svc = MemoryConsolidationService(db)
        stats = await svc.consolidate_memories()

        # Verify candidate rule and task were created
        assert stats["rule_candidates_created"] == 1
        assert stats["merge_tasks_created"] == 1
        assert db.add.call_count >= 2  # One AIRule + One AITask

        added_items = [arg[0] for arg, kw in db.add.call_args_list]
        rule_item = [x for x in added_items if isinstance(x, AIRule)][0]

        assert rule_item.status == "draft"  # High risk, never auto-activated
        assert "company currency" in rule_item.title.lower()
