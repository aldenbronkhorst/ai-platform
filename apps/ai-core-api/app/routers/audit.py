from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import AUDIT_ROLES, require_role
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate, AIAuditEventResponse
from typing import Optional
from uuid import UUID

router = APIRouter(prefix="/audit", tags=["audit"])


@router.post("", response_model=AIAuditEventResponse, status_code=status.HTTP_201_CREATED)
async def create_audit_event(
    data: AIAuditEventCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(require_role(["AIPlatform.Admin"])),
):
    svc = AuditService(db)
    event = await svc.log_event(data)
    return event


@router.get("", response_model=list[AIAuditEventResponse])
async def list_audit_events(
    job_id: Optional[UUID] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    auth=Depends(require_role(list(AUDIT_ROLES))),
):
    svc = AuditService(db)
    return await svc.get_events(job_id=job_id, limit=limit, offset=offset)
