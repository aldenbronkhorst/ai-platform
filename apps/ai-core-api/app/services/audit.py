from sqlalchemy.ext.asyncio import AsyncSession
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
