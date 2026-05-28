from typing import Optional, List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.models.models import AITool
from app.schemas.schemas import AIToolCreate


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

    async def list_tools(self, target_system: Optional[str] = None, status: str = "active") -> List[AITool]:
        query = select(AITool).where(AITool.status == status)
        if target_system:
            query = query.where(AITool.target_system == target_system)
        query = query.order_by(AITool.name)
        result = await self.db.execute(query)
        return result.scalars().all()
