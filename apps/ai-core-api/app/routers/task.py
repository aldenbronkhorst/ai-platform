from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import dev_api_key_auth
from app.services.task import TaskService
from app.services.audit import AuditService
from app.schemas.schemas import AITaskCreate, AITaskResponse, AITaskUpdate, AIAuditEventCreate
from uuid import UUID

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=AITaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: AITaskCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(dev_api_key_auth),
):
    svc = TaskService(db)
    task = await svc.create(data, created_by_user_id=auth.get("user_id"))

    # Audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="create",
        target_system="ai-platform",
        target_model="ai_tasks",
        target_record_id=str(task.id),
        input_summary=f"Created task: {data.title}",
        risk_level="low",
        status="success",
    ))

    return task


@router.get("", response_model=list[AITaskResponse])
async def list_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    auth=Depends(dev_api_key_auth),
):
    svc = TaskService(db)
    return await svc.list_tasks(status=status, owner_user_id=auth.get("user_id"), limit=limit, offset=offset)


@router.patch("/{task_id}", response_model=AITaskResponse)
async def update_task(
    task_id: UUID,
    data: AITaskUpdate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(dev_api_key_auth),
):
    svc = TaskService(db)
    task = await svc.update(task_id, data)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # Audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="update",
        target_system="ai-platform",
        target_model="ai_tasks",
        target_record_id=str(task_id),
        input_summary=f"Updated task {task_id}: {data.model_dump_json(exclude_unset=True)}",
        risk_level="low",
        status="success",
    ))

    return task
