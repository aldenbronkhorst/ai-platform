from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import DEVELOPER_ROLES, api_key_auth, require_role
from app.services.tool import ToolService
from app.services.audit import AuditService
from app.schemas.schemas import AIToolCreate, AIToolResponse, AIAuditEventCreate
from uuid import UUID

router = APIRouter(prefix="/tools", tags=["tools"])


@router.post("/register", response_model=AIToolResponse, status_code=status.HTTP_201_CREATED)
async def register_tool(
    data: AIToolCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(require_role(list(DEVELOPER_ROLES))),
):
    svc = ToolService(db)
    existing = await svc.get_by_name(data.name)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Tool {data.name} already exists")

    tool = await svc.create(data, created_by_user_id=auth.get("user_id"))

    # Audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="create",
        target_system="ai-platform",
        target_model="ai_tools",
        target_record_id=str(tool.id),
        input_summary=f"Registered tool: {data.name} for {data.target_system}",
        risk_level="low",
        status="success",
    ))

    return tool


@router.get("", response_model=list[AIToolResponse])
async def list_tools(
    target_system: str = None,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ToolService(db)
    return await svc.list_tools(target_system=target_system)
