from typing import Optional, List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.models.models import AITool
from app.schemas.schemas import AIToolCreate
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, CONNECTOR_SYSTEMS, is_model_facing_tool


class ToolService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: AIToolCreate, created_by_user_id: Optional[UUID] = None) -> AITool:
        tool = AITool(
            **data.model_dump(exclude_unset=True),
            created_by_user_id=created_by_user_id,
        )
        self.db.add(tool)
        await self.db.flush()
        return tool

    async def get_by_id(self, tool_id: UUID) -> Optional[AITool]:
        result = await self.db.execute(select(AITool).where(AITool.id == tool_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[AITool]:
        result = await self.db.execute(select(AITool).where(AITool.name == name))
        return result.scalar_one_or_none()

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
        return [tool for tool in tools if is_model_facing_tool(tool.name, tool.target_system)]
