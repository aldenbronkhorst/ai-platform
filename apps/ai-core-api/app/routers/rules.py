"""Business Rules router: read-only AIRule listing for the admin dashboard."""
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIRule
from app.schemas.schemas import AIRuleResponse

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=List[AIRuleResponse])
async def list_rules(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """List business rules for the admin dashboard and model-context review."""
    query = select(AIRule)
    if status_filter:
        query = query.where(AIRule.status == status_filter)
    query = query.order_by(AIRule.priority).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
