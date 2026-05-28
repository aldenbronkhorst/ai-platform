from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.context import ContextService
from app.schemas.schemas import ContextRequest, ContextResponse, AIRuleResponse, AICompanyFactResponse, AIToolResponse

router = APIRouter(prefix="/context", tags=["context"])


@router.post("", response_model=ContextResponse)
async def get_context(
    req: ContextRequest,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ContextService(db)
    result = await svc.get_context(req)
    return {
        "rules": [AIRuleResponse.model_validate(r) for r in result["rules"]],
        "facts": [AICompanyFactResponse.model_validate(f) for f in result["facts"]],
        "tools": [AIToolResponse.model_validate(t) for t in result["tools"]],
    }
