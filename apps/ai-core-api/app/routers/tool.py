from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.tool import ToolService
from app.schemas.schemas import AIToolResponse

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[AIToolResponse])
async def list_tools(
    target_system: str = None,
    include_internal: bool = Query(False, description="Include non-canonical/internal connector tools"),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ToolService(db)
    return await svc.list_tools(target_system=target_system, include_internal=include_internal)
