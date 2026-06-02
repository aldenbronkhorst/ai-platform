from typing import Optional, List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from app.models.models import AITask
from app.schemas.schemas import AITaskCreate, AITaskUpdate


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: AITaskCreate, created_by_user_id: Optional[UUID] = None) -> AITask:
        values = data.model_dump(exclude_unset=True)
        if not values.get("owner_user_id"):
            values["owner_user_id"] = created_by_user_id
        task = AITask(
            **values,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(task)
        await self.db.flush()
        return task

    async def get_by_id(self, task_id: UUID) -> Optional[AITask]:
        result = await self.db.execute(select(AITask).where(AITask.id == task_id))
        return result.scalar_one_or_none()

    async def list_tasks(self, status: Optional[str] = None, owner_user_id: Optional[UUID] = None, limit: int = 50, offset: int = 0) -> List[AITask]:
        query = select(AITask).order_by(AITask.created_at.desc())
        filters = []
        if status:
            filters.append(AITask.status == status)
        if owner_user_id:
            filters.append(or_(AITask.owner_user_id == owner_user_id, AITask.created_by_user_id == owner_user_id))
        if filters:
            query = query.where(and_(*filters))
        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def update(self, task_id: UUID, data: AITaskUpdate) -> Optional[AITask]:
        task = await self.get_by_id(task_id)
        if task:
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(task, field, value)
            await self.db.flush()
        return task
