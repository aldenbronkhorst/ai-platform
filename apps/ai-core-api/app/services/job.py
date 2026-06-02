from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.models.models import AIJob
from app.schemas.schemas import AIJobCreate


class JobService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: AIJobCreate, requested_by_user_id: Optional[UUID] = None) -> AIJob:
        job = AIJob(
            **data.model_dump(exclude_unset=True),
            requested_by_user_id=requested_by_user_id,
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def get_by_id(self, job_id: UUID) -> Optional[AIJob]:
        result = await self.db.execute(select(AIJob).where(AIJob.id == job_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: Optional[UUID], limit: int = 50, offset: int = 0) -> list[AIJob]:
        stmt = select(AIJob).order_by(desc(AIJob.created_at)).limit(limit).offset(offset)
        if user_id:
            stmt = stmt.where(AIJob.requested_by_user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(self, job_id: UUID, status: str, current_step: Optional[str] = None, summary: Optional[str] = None) -> Optional[AIJob]:
        job = await self.get_by_id(job_id)
        if job:
            job.status = status
            if current_step:
                job.current_step = current_step
            if summary:
                job.summary = summary
            await self.db.flush()
        return job
