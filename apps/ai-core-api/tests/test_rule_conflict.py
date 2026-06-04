import pytest
from uuid import uuid4, UUID
from unittest.mock import patch, MagicMock, AsyncMock

from app.models.models import AIRule, AITask
from app.services.rule_conflict import RuleConflictService
from tests.test_model_router import MockSession


class TestRuleConflictService:
    @pytest.mark.asyncio
    async def test_currency_conflict_detected(self):
        db = MockSession(has_config=False)

        # Existing rule: Always ZAR
        old_rule = AIRule(
            id=uuid4(),
            title="Standard Company Currency",
            body="Always use company currency ZAR for bills.",
            scope_type="global",
            status="active"
        )

        # New rule: Always USD
        new_rule = AIRule(
            id=uuid4(),
            title="USD reporting",
            body="Always show financial values in USD.",
            scope_type="global",
            status="active"
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [old_rule])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = RuleConflictService(db)
        conflict = await svc.check_conflicts(new_rule)

        assert conflict is not None
        assert conflict["severity"] == "high"
        assert conflict["conflicting_rule_id"] == str(old_rule.id)
        assert conflict["recommended_action"] == "reject_new"
        assert "'usd' vs 'zar'" in conflict["opposing_terms"]

    @pytest.mark.asyncio
    async def test_revenue_definition_conflict(self):
        db = MockSession(has_config=False)

        old_rule = AIRule(
            id=uuid4(),
            title="Revenue posted definition",
            body="Revenue means posted invoices only.",
            scope_type="global",
            status="active"
        )

        new_rule = AIRule(
            id=uuid4(),
            title="Include draft invoices in revenue",
            body="Revenue should include draft invoices.",
            scope_type="global",
            status="active"
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [old_rule])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = RuleConflictService(db)
        conflict = await svc.check_conflicts(new_rule)

        assert conflict is not None
        assert conflict["severity"] == "high"
        assert "'posted' vs 'draft'" in conflict["opposing_terms"]

    @pytest.mark.asyncio
    async def test_user_delegated_identity_conflict_detected(self):
        db = MockSession(has_config=False)

        old_rule = AIRule(
            id=uuid4(),
            title="User Identity Policy",
            body=(
                "Direct user-triggered actions must use the requesting user's connected "
                "account wherever possible."
            ),
            scope_type="global",
            status="active"
        )

        new_rule = AIRule(
            id=uuid4(),
            title="Conflicting User Identity Policy",
            body=(
                "Direct user-triggered actions must never use user connected accounts. "
                "In addition, always block user-delegated authentication."
            ),
            scope_type="global",
            status="active"
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [old_rule])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = RuleConflictService(db)
        conflict = await svc.check_conflicts(new_rule)

        assert conflict is not None
        assert conflict["severity"] == "high"
        assert (
            "'user-delegated access allowed' vs 'user-delegated access blocked'"
            in conflict["opposing_terms"]
        )

    @pytest.mark.asyncio
    async def test_non_conflicting_different_scopes(self):
        db = MockSession(has_config=False)

        # Rule for customer ABC
        old_rule = AIRule(
            id=uuid4(),
            title="ABC prioritize",
            body="Always prioritize ABC customers.",
            scope_type="customer",
            scope_value="ABC",
            status="active"
        )

        # Rule for customer XYZ
        new_rule = AIRule(
            id=uuid4(),
            title="XYZ deprioritize",
            body="Always deprioritize XYZ customers.",
            scope_type="customer",
            scope_value="XYZ",
            status="active"
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [old_rule])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = RuleConflictService(db)
        conflict = await svc.check_conflicts(new_rule)

        # No conflict because scopes are completely different!
        assert conflict is None

    @pytest.mark.asyncio
    async def test_governance_enforcement_high_risk_conflict(self):
        db = MockSession(has_config=False)

        old_rule = AIRule(
            id=uuid4(),
            title="Standard Company Currency",
            body="Always use company currency ZAR for bills.",
            scope_type="global",
            status="active"
        )

        new_rule = AIRule(
            id=uuid4(),
            title="USD reporting",
            body="Always show financial values in USD.",
            scope_type="global",
            status="active"
        )

        class QueryResult:
            @property
            def scalars(self):
                return lambda: MagicMock(all=lambda: [old_rule])

        db.execute = AsyncMock(return_value=QueryResult())
        db.add = MagicMock()

        svc = RuleConflictService(db)
        gated = await svc.enforce_rule_governance(new_rule)

        assert gated is True
        assert new_rule.status == "draft"  # Forced to draft due to high-risk conflict!
        
        # Verify an AITask was added to database
        added_items = [arg[0] for arg, kw in db.add.call_args_list]
        task_item = [x for x in added_items if isinstance(x, AITask)][0]
        assert task_item.priority == "high"
        assert str(new_rule.id) in task_item.description
