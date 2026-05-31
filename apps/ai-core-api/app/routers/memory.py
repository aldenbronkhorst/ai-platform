"""Memory router: CRUD for AIMemory, memory candidate extraction, approval workflow.

Supports the Memory Agent lifecycle: list, create, update, approve, archive, and
retrieve relevant memories for context injection.
"""
import logging
from datetime import datetime
from typing import Optional, List
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIMemory, AIChatMessage
from app.schemas.schemas import AIMemoryCreate, AIMemoryUpdate, AIMemoryResponse, MemoryCandidate
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=List[AIMemoryResponse])
async def list_memories(
    type: Optional[str] = Query(None, description="Filter by memory type"),
    status: Optional[str] = Query(None, description="Filter by status (active, draft, archived, rejected, needs_review)"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level (low, medium, high)"),
    scope_type: Optional[str] = Query(None, description="Filter by scope type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """List memories with optional filtering. Ordered by created_at descending."""
    query = select(AIMemory)

    if type:
        query = query.where(AIMemory.type == type)
    if status:
        query = query.where(AIMemory.status == status)
    if risk_level:
        query = query.where(AIMemory.risk_level == risk_level)
    if scope_type:
        query = query.where(AIMemory.scope_type == scope_type)

    query = query.order_by(desc(AIMemory.created_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{memory_id}", response_model=AIMemoryResponse)
async def get_memory(
    memory_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Get a single memory by ID."""
    result = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.post("", response_model=AIMemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    req: AIMemoryCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Create a new memory record directly."""
    user_id = auth.get("user_id")
    memory = AIMemory(
        id=uuid4(),
        type=req.type,
        title=req.title,
        summary=req.summary,
        body=req.body,
        scope_type=req.scope_type,
        scope_value=req.scope_value,
        entities_json=req.entities_json,
        source_type=req.source_type,
        source_id=req.source_id,
        conversation_id=req.conversation_id,
        message_id=req.message_id,
        confidence=req.confidence,
        risk_level=req.risk_level,
        status=req.status,
        priority=req.priority,
        success_count=0,
        failure_count=0,
        version=1,
        created_by_user_id=user_id,
        metadata_json=req.metadata_json,
    )
    db.add(memory)
    await db.flush()

    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="memory_created",
        target_model="ai_memories",
        target_record_id=str(memory.id),
        actor_user_id=user_id,
        input_summary=f"Created memory: {req.type} - {req.title}",
        risk_level=req.risk_level,
        status="success",
    ))
    await db.commit()
    await db.refresh(memory)
    if memory.status == "active":
        try:
            from app.services.search_service import SearchService
            search_svc = SearchService()
            await search_svc.index_memory_record(memory)
        except Exception as e:
            logger.warning("Failed to index created memory in search service: %s", e)
    logger.info("Memory created | id=%s type=%s risk=%s", memory.id, memory.type, memory.risk_level)
    return memory


@router.patch("/{memory_id}", response_model=AIMemoryResponse)
async def update_memory(
    memory_id: UUID,
    req: AIMemoryUpdate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Update a memory's metadata or status."""
    result = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    user_id = auth.get("user_id")
    changed = []
    if req.title is not None:
        memory.title = req.title
        changed.append("title")
    if req.summary is not None:
        memory.summary = req.summary
        changed.append("summary")
    if req.body is not None:
        memory.body = req.body
        changed.append("body")
    if req.status is not None:
        memory.status = req.status
        changed.append(f"status={req.status}")
        if req.status == "active":
            memory.approved_by_user_id = user_id
    if req.confidence is not None:
        memory.confidence = req.confidence
        changed.append("confidence")
    if req.priority is not None:
        memory.priority = req.priority
        changed.append("priority")

    memory.updated_at = datetime.utcnow()

    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="memory_updated",
        target_model="ai_memories",
        target_record_id=str(memory.id),
        actor_user_id=user_id,
        input_summary=f"Updated memory {memory_id}: {', '.join(changed)}",
        risk_level="low",
        status="success",
    ))
    await db.commit()
    await db.refresh(memory)
    try:
        from app.services.search_service import SearchService
        search_svc = SearchService()
        if memory.status == "active":
            await search_svc.index_memory_record(memory)
        elif memory.status in ("archived", "rejected"):
            await search_svc.delete_memory_record(memory.id)
    except Exception as e:
        logger.warning("Failed to sync updated memory with search index: %s", e)
    logger.info("Memory updated | id=%s changes=%s", memory.id, changed)
    return memory


@router.post("/{memory_id}/approve", response_model=AIMemoryResponse)
async def approve_memory(
    memory_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Approve a draft/needs_review memory, setting it to active."""
    result = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    user_id = auth.get("user_id")
    previous_status = memory.status
    memory.status = "active"
    memory.approved_by_user_id = user_id
    memory.last_confirmed_at = datetime.utcnow()
    memory.updated_at = datetime.utcnow()

    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="memory_approved",
        target_model="ai_memories",
        target_record_id=str(memory.id),
        actor_user_id=user_id,
        input_summary=f"Approved memory '{memory.title}' (was {previous_status})",
        risk_level="medium",
        status="success",
    ))
    await db.commit()
    await db.refresh(memory)
    try:
        from app.services.search_service import SearchService
        search_svc = SearchService()
        await search_svc.index_memory_record(memory)
    except Exception as e:
        logger.warning("Failed to index approved memory into search index: %s", e)
    logger.info("Memory approved | id=%s title=%s", memory.id, memory.title)
    return memory


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_memory(
    memory_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Soft-delete/archive a memory."""
    result = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    user_id = auth.get("user_id")
    memory.status = "archived"
    memory.updated_at = datetime.utcnow()

    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="memory_archived",
        target_model="ai_memories",
        target_record_id=str(memory.id),
        actor_user_id=user_id,
        input_summary=f"Archived memory '{memory.title}'",
        risk_level="low",
        status="success",
    ))
    await db.commit()
    try:
        from app.services.search_service import SearchService
        search_svc = SearchService()
        await search_svc.delete_memory_record(memory_id)
    except Exception as e:
        logger.warning("Failed to delete archived memory from search index: %s", e)
    logger.info("Memory archived | id=%s title=%s", memory.id, memory.title)


@router.post("/extract", response_model=List[MemoryCandidate])
async def extract_memory_candidates(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Extract memory candidates from a completed conversation."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIChatMessage).where(
            AIChatMessage.chat_session_id == conversation_id,
            AIChatMessage.user_id == str(user_id),
        ).order_by(AIChatMessage.created_at.asc())
    )
    messages = result.scalars().all()

    from app.services.memory import MemoryCandidateService
    svc = MemoryCandidateService(db)
    candidates = await svc.extract_from_messages(messages=messages, user_id=user_id)

    logger.info(
        "Memory candidates extracted | conversation=%s count=%d user_id=%s",
        conversation_id, len(candidates), user_id,
    )
    return candidates


@router.post("/save-candidate", response_model=AIMemoryResponse, status_code=status.HTTP_201_CREATED)
async def save_memory_candidate(
    candidate: MemoryCandidate,
    conversation_id: Optional[UUID] = Query(None),
    message_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Save a memory candidate directly. Frontend calls this when user clicks Save."""
    user_id = auth.get("user_id")

    from app.services.memory import MemoryCandidateService
    svc = MemoryCandidateService(db)

    # Check duplicate
    is_dup = await svc.check_duplicate(candidate)
    if is_dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A similar active memory already exists. Edit the existing one instead.",
        )

    memory = await svc.save_candidate(
        candidate=candidate,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    await db.commit()
    await db.refresh(memory)
    if memory.status == "active":
        try:
            from app.services.search_service import SearchService
            search_svc = SearchService()
            await search_svc.index_memory_record(memory)
        except Exception as e:
            logger.warning("Failed to index candidate memory into search index: %s", e)
    return memory


@router.post("/review", status_code=status.HTTP_200_OK)
async def trigger_memory_review(
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Triggers the memory review, conflict detection, and cleanup job."""
    from app.services.memory_review import MemoryReviewService
    svc = MemoryReviewService(db)
    result = await svc.run_review_job()
    await db.commit()
    return result
