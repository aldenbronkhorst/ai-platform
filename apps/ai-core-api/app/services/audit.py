from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.models import AIAuditEvent
from app.schemas.schemas import AIAuditEventCreate


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log_event(self, data: AIAuditEventCreate) -> AIAuditEvent:
        event = AIAuditEvent(**data.model_dump(exclude_unset=True))
        self.db.add(event)
        await self.db.flush()
        return event

    async def get_events(self, job_id: Optional[UUID] = None, limit: int = 50, offset: int = 0):
        query = select(AIAuditEvent).order_by(AIAuditEvent.timestamp.desc())
        if job_id:
            query = query.where(AIAuditEvent.job_id == job_id)
        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        return result.scalars().all()
