"""Business Rules router: CRUD for AIRule from the admin dashboard."""
import logging
import uuid as uuid_pkg
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIRule
from app.schemas.schemas import AIRuleResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rules", tags=["rules"])


class AIRuleCreate(BaseModel):
    title: str
    body: str
    scope_type: Optional[str] = "global"
    scope_value: Optional[str] = None
    department: Optional[str] = None
    workflow: Optional[str] = None
    supplier: Optional[str] = None
    customer: Optional[str] = None
    status: str = "active"
    priority: int = 100


class AIRuleUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None


@router.get("", response_model=List[AIRuleResponse])
async def list_rules(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """List all business rules for the admin dashboard."""
    query = select(AIRule)
    if status_filter:
        query = query.where(AIRule.status == status_filter)
    query = query.order_by(AIRule.priority).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=AIRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    req: AIRuleCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Create a new business rule."""
    rule = AIRule(
        id=uuid_pkg.uuid4(),
        title=req.title,
        body=req.body,
        scope_type=req.scope_type,
        scope_value=req.scope_value,
        department=req.department,
        workflow=req.workflow,
        supplier=req.supplier,
        customer=req.customer,
        status=req.status,
        priority=req.priority,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    logger.info("Rule created | id=%s title=%s", rule.id, rule.title)
    return rule


@router.patch("/{rule_id}", response_model=AIRuleResponse)
async def update_rule(
    rule_id: UUID,
    req: AIRuleUpdate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Update a business rule."""
    result = await db.execute(select(AIRule).where(AIRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if req.title is not None:
        rule.title = req.title
    if req.body is not None:
        rule.body = req.body
    if req.status is not None:
        rule.status = req.status
    if req.priority is not None:
        rule.priority = req.priority

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Delete an archived/inactive rule."""
    result = await db.execute(select(AIRule).where(AIRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    logger.info("Rule deleted | id=%s title=%s", rule.id, rule.title)
