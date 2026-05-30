from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.context import ContextService
from app.schemas.schemas import ContextRequest, ContextResponse, AIRuleResponse, AICompanyFactResponse, AIToolResponse
from app.models.models import AIRule

router = APIRouter(prefix="/context", tags=["context"])


@router.get("/rules", response_model=List[AIRuleResponse])
async def list_rules(
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """List all active business rules for the admin dashboard."""
    result = await db.execute(
        select(AIRule).order_by(AIRule.priority)
    )
    return result.scalars().all()


@router.post("", response_model=ContextResponse)
async def get_context(
    req: ContextRequest,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    user_id = auth.get("user_id")
    svc = ContextService(db)
    result = await svc.get_context(req, user_id=user_id)
    return {
        "rules": [AIRuleResponse.model_validate(r) for r in result["rules"]],
        "facts": [AICompanyFactResponse.model_validate(f) for f in result["facts"]],
        "tools": [AIToolResponse.model_validate(t) for t in result["tools"]],
    }
