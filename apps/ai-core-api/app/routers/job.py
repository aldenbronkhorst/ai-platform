from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.job import JobService
from app.services.audit import AuditService
from app.schemas.schemas import AIJobCreate, AIJobResponse, AIAuditEventCreate
from uuid import UUID

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=AIJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: AIJobCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = JobService(db)
    job = await svc.create(data, requested_by_user_id=auth.get("user_id"))

    # Audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="create",
        target_system="ai-platform",
        target_model="ai_jobs",
        target_record_id=str(job.id),
        job_id=job.id,
        input_summary=f"Created job: {data.title} type={data.workflow_type}",
        risk_level="low",
        status="success",
    ))

    return job


@router.get("/{job_id}", response_model=AIJobResponse)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = JobService(db)
    job = await svc.get_by_id(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
