from typing import List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from app.models.models import AIRule, AICompanyFact, AITool
from app.schemas.schemas import ContextRequest


class ContextService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_context(self, req: ContextRequest) -> dict:
        # Fetch relevant rules
        rules_query = select(AIRule).where(
            and_(
                AIRule.status == "active",
                or_(
                    AIRule.effective_from.is_(None),
                    AIRule.effective_from <= __import__('datetime').datetime.utcnow()
                ),
                or_(
                    AIRule.effective_to.is_(None),
                    AIRule.effective_to >= __import__('datetime').datetime.utcnow()
                )
            )
        )
        if req.supplier:
            rules_query = rules_query.where(
                or_(AIRule.supplier == req.supplier, AIRule.supplier.is_(None), AIRule.scope_type == "global")
            )
        if req.customer:
            rules_query = rules_query.where(
                or_(AIRule.customer == req.customer, AIRule.customer.is_(None), AIRule.scope_type == "global")
            )
        if req.department:
            rules_query = rules_query.where(
                or_(AIRule.department == req.department, AIRule.department.is_(None), AIRule.scope_type == "global")
            )
        if req.workflow:
            rules_query = rules_query.where(
                or_(AIRule.workflow == req.workflow, AIRule.workflow.is_(None), AIRule.scope_type == "global")
            )
        rules_query = rules_query.order_by(AIRule.priority).limit(req.limit)
        rules_result = await self.db.execute(rules_query)
        rules = rules_result.scalars().all()

        # Fetch relevant facts
        facts_query = select(AICompanyFact).where(
            or_(
                AICompanyFact.effective_from.is_(None),
                AICompanyFact.effective_from <= __import__('datetime').datetime.utcnow()
            )
        )
        if req.department:
            facts_query = facts_query.where(
                or_(AICompanyFact.category == req.department, AICompanyFact.category.is_(None))
            )
        facts_query = facts_query.limit(req.limit)
        facts_result = await self.db.execute(facts_query)
        facts = facts_result.scalars().all()

        # Fetch relevant tools for the requested systems
        tools_query = select(AITool).where(AITool.status == "active")
        if req.systems:
            tools_query = tools_query.where(AITool.target_system.in_(req.systems))
        tools_query = tools_query.limit(req.limit)
        tools_result = await self.db.execute(tools_query)
        tools = tools_result.scalars().all()

        return {
            "rules": rules,
            "facts": facts,
            "tools": tools,
        }
