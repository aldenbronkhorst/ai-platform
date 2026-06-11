"""Context service: builds scoped context for the model from facts and tools.

Respects connector availability so disconnected systems are not injected as
available capabilities.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.models.models import AICompanyFact, AITool, AIConnectedAccount
from app.schemas.schemas import ContextRequest
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, CONNECTOR_SYSTEMS, is_model_facing_tool


class ContextService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_connected_systems(self, user_id: Optional[UUID]) -> set[str]:
        """Return the set of systems the user has connected accounts for."""
        if not user_id:
            return set()
        result = await self.db.execute(
            select(AIConnectedAccount).where(
                AIConnectedAccount.user_id == user_id,
                or_(
                    AIConnectedAccount.status == "connected",
                    AIConnectedAccount.status == "active",
                ),
            )
        )
        accounts = result.scalars().all()
        return {a.provider for a in accounts}

    async def get_context(
        self,
        req: ContextRequest,
        user_id: Optional[UUID] = None,
        connected_systems: Optional[set[str]] = None,
    ) -> dict:
        connected_systems = connected_systems if connected_systems is not None else await self._get_connected_systems(user_id)
        now = datetime.now(timezone.utc)

        # ── Fetch relevant facts (limit to connected systems unless global) ──
        facts_query = select(AICompanyFact).where(
            or_(
                AICompanyFact.effective_from.is_(None),
                AICompanyFact.effective_from <= now
            )
        )
        if req.department:
            facts_query = facts_query.where(
                or_(AICompanyFact.category == req.department, AICompanyFact.category.is_(None))
            )
        facts_query = facts_query.limit(req.limit)
        facts_result = await self.db.execute(facts_query)
        facts = facts_result.scalars().all()

        # Filter facts: exclude system-connector facts for disconnected systems
        SYSTEM_FACT_PREFIXES = ("odoo_", "github_", "azure_", "m365_")
        filtered_facts = []
        for fact in facts:
            should_skip = False
            for prefix in SYSTEM_FACT_PREFIXES:
                if fact.key.startswith(prefix):
                    system_name = prefix.rstrip("_")
                    if system_name not in connected_systems:
                        should_skip = True
                        break
            if not should_skip:
                filtered_facts.append(fact)

        # ── Fetch relevant tools (only for connected or requested systems) ──
        tools_query = select(AITool).where(AITool.status == "active")

        # Determine which systems to include: requested systems OR connected systems
        target_systems = set()
        if req.systems:
            target_systems.update(req.systems)
        target_systems.update(connected_systems)

        if target_systems:
            tools_query = tools_query.where(AITool.target_system.in_(target_systems))
        else:
            # No connected or requested systems: only show AI-platform tools
            tools_query = tools_query.where(AITool.target_system == "ai-platform")
        tools_query = tools_query.where(
            or_(
                ~AITool.target_system.in_(CONNECTOR_SYSTEMS),
                AITool.name.in_(CONSOLIDATED_TOOL_NAMES),
            )
        )

        tools_query = tools_query.limit(req.limit)
        tools_result = await self.db.execute(tools_query)
        tools = [
            tool
            for tool in tools_result.scalars().all()
            if is_model_facing_tool(tool.name, tool.target_system)
        ]

        return {
            "facts": filtered_facts,
            "tools": tools,
        }
