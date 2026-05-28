from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
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
