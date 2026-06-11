from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.models.models import AITool
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, CONNECTOR_SYSTEMS, is_model_facing_tool


class ToolService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_tools(
        self,
        target_system: Optional[str] = None,
        status: str = "active",
        include_internal: bool = False,
    ) -> List[AITool]:
        query = select(AITool).where(AITool.status == status)
        if target_system:
            query = query.where(AITool.target_system == target_system)
        if not include_internal:
            query = query.where(
                or_(
                    ~AITool.target_system.in_(CONNECTOR_SYSTEMS),
                    AITool.name.in_(CONSOLIDATED_TOOL_NAMES),
                )
            )
        query = query.order_by(AITool.name)
        result = await self.db.execute(query)
        tools = result.scalars().all()
        if include_internal:
            return tools
        return _dedupe_visible_tools([tool for tool in tools if is_model_facing_tool(tool.name, tool.target_system)])


def _dedupe_visible_tools(tools: List[AITool]) -> List[AITool]:
    seen: set[tuple[str, str]] = set()
    unique: List[AITool] = []
    for tool in tools:
        key = ((tool.display_name or tool.name).strip().lower(), tool.target_system)
        if key in seen:
            continue
        seen.add(key)
        unique.append(tool)
    return unique
