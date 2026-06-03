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
from app.core.security import AUDIT_ROLES, DEVELOPER_ROLES, api_key_auth, has_role, require_auth_role
from app.models.models import AIMemory, AIChatMessage, AIMemoryUsageEvent, AITask
from app.schemas.schemas import AIMemoryCreate, AIMemoryUpdate, AIMemoryResponse, MemoryCandidate, MemoryFeedbackRequest
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])


def _can_read_memory(auth: dict, memory: AIMemory) -> bool:
    user_id = auth.get("user_id")
    if memory.created_by_user_id == user_id:
        return True
    if memory.scope_type == "global" and memory.status == "active":
        return True
    return has_role(auth, DEVELOPER_ROLES | AUDIT_ROLES)


def _can_manage_memory(auth: dict, memory: AIMemory) -> bool:
    user_id = auth.get("user_id")
    return memory.created_by_user_id == user_id or has_role(auth, DEVELOPER_ROLES)


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
    user_id = auth.get("user_id")

    if type:
        query = query.where(AIMemory.type == type)
    if status:
        query = query.where(AIMemory.status == status)
    if risk_level:
        query = query.where(AIMemory.risk_level == risk_level)
    if scope_type:
        query = query.where(AIMemory.scope_type == scope_type)
    if not has_role(auth, DEVELOPER_ROLES | AUDIT_ROLES):
        query = query.where(
            or_(
                AIMemory.created_by_user_id == user_id,
                and_(AIMemory.scope_type == "global", AIMemory.status == "active"),
            )
        )

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
    if not _can_read_memory(auth, memory):
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
    if not _can_manage_memory(auth, memory):
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
    if not _can_manage_memory(auth, memory):
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
    if not _can_manage_memory(auth, memory):
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
            AIChatMessage.user_id == user_id,
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
    require_auth_role(auth, DEVELOPER_ROLES, "Memory review is reserved for platform maintainers.")
    from app.services.memory_review import MemoryReviewService
    svc = MemoryReviewService(db)
    result = await svc.run_review_job()
    await db.commit()
    return result


@router.post("/consolidate", status_code=status.HTTP_200_OK)
async def trigger_memory_consolidation(
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    """Triggers the memory review and consolidation pipeline."""
    require_auth_role(auth, DEVELOPER_ROLES, "Memory consolidation is reserved for platform maintainers.")
    from app.services.memory_consolidation import MemoryConsolidationService
    svc = MemoryConsolidationService(db)
    result = await svc.consolidate_memories()
    await db.commit()
    return result


async def _get_feedback_memory(db: AsyncSession, memory_id: UUID, auth: dict) -> AIMemory:
    mem_q = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    memory = mem_q.scalar_one_or_none()
    if not memory or not _can_read_memory(auth, memory):
        raise HTTPException(status_code=404, detail="Memory not found.")
    return memory


async def _upsert_memory_usage_event(
    db: AsyncSession,
    *,
    memory_id: UUID,
    req: MemoryFeedbackRequest,
    user_id: str,
    feedback_type: str,
) -> None:
    usage_event = None
    if req.chat_message_id:
        evt_q = await db.execute(
            select(AIMemoryUsageEvent).where(
                AIMemoryUsageEvent.memory_id == memory_id,
                AIMemoryUsageEvent.chat_message_id == req.chat_message_id,
            )
        )
        usage_event = evt_q.scalars().first()

    if usage_event:
        usage_event.feedback_type = feedback_type
        usage_event.feedback_value = req.comment
        return

    db.add(AIMemoryUsageEvent(
        id=uuid4(),
        memory_id=memory_id,
        chat_message_id=req.chat_message_id,
        user_id=user_id,
        feedback_type=feedback_type,
        feedback_value=req.comment,
        created_at=datetime.utcnow(),
    ))


def _increase_memory_confidence(memory: AIMemory) -> str:
    if memory.confidence in {"low", "medium"}:
        memory.confidence = "medium" if memory.confidence == "low" else "high"
        return "memory_confidence_increased"
    return "memory_feedback_recorded"


def _decrease_memory_confidence(memory: AIMemory) -> str:
    if memory.confidence in {"high", "medium"}:
        memory.confidence = "medium" if memory.confidence == "high" else "low"
        return "memory_confidence_decreased"
    return "memory_feedback_recorded"


def _feedback_requires_review(memory: AIMemory, feedback_type: str) -> bool:
    return feedback_type in {"needs_review", "outdated"} or (memory.failure_count or 0) > 3


def _apply_memory_feedback(memory: AIMemory, feedback_type: str) -> tuple[str, bool]:
    if feedback_type in {"helpful", "worked"}:
        memory.success_count = (memory.success_count or 0) + 1
        memory.last_confirmed_at = datetime.utcnow()
        return _increase_memory_confidence(memory), False

    if feedback_type in {"wrong", "outdated", "do_not_use", "needs_review"}:
        memory.failure_count = (memory.failure_count or 0) + 1
        audit_action = _decrease_memory_confidence(memory)
        if _feedback_requires_review(memory, feedback_type):
            memory.status = "needs_review"
            return "memory_flagged_for_review", True
        return audit_action, False

    if feedback_type == "not_relevant":
        return _decrease_memory_confidence(memory), False

    return "memory_feedback_recorded", False


def _add_memory_review_task(db: AsyncSession, memory: AIMemory, feedback_type: str, comment: str | None) -> None:
    db.add(AITask(
        id=uuid4(),
        title=f"Memory Review Required: {memory.title}",
        description=f"Memory (id={memory.id}) has been flagged as '{feedback_type}' by user feedback. Comment: '{comment or ''}'.",
        status="open",
        priority="high",
        linked_model="ai_memories",
        linked_record_id=str(memory.id),
    ))


async def _log_memory_feedback(
    db: AsyncSession,
    *,
    memory: AIMemory,
    user_id: str,
    feedback_type: str,
    audit_action: str,
    old_confidence: str,
    old_status: str,
    create_review_task: bool,
) -> None:
    await AuditService(db).log_event(AIAuditEventCreate(
        action_type=audit_action,
        target_model="ai_memories",
        target_record_id=str(memory.id),
        actor_user_id=user_id,
        input_summary=(
            f"Memory feedback '{feedback_type}' received. Confidence: {old_confidence} -> {memory.confidence}. "
            f"Status: {old_status} -> {memory.status}."
        ),
        risk_level="medium" if create_review_task else "low",
        status="success",
    ))


async def _delete_memory_from_search_if_inactive(memory: AIMemory) -> None:
    if memory.status == "active":
        return
    try:
        from app.services.search_service import SearchService
        await SearchService().delete_memory_record(memory.id)
    except Exception as e:
        logger.warning("Failed to delete memory from search index during feedback: %s", e)


@router.post("/{memory_id}/feedback", response_model=AIMemoryResponse)
async def record_memory_feedback(
    memory_id: UUID,
    req: MemoryFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Submit user feedback for a specific memory record.

    Increments success/failure counts, adjusts confidence, and flags for review if needed.
    """
    user_id = auth.get("user_id")

    memory = await _get_feedback_memory(db, memory_id, auth)
    old_confidence = memory.confidence
    old_status = memory.status
    f_type = req.feedback_type.strip().lower()

    await _upsert_memory_usage_event(db, memory_id=memory_id, req=req, user_id=user_id, feedback_type=f_type)
    audit_action, create_review_task = _apply_memory_feedback(memory, f_type)
    memory.updated_at = datetime.utcnow()

    if create_review_task:
        _add_memory_review_task(db, memory, f_type, req.comment)

    await _log_memory_feedback(
        db,
        memory=memory,
        user_id=user_id,
        feedback_type=f_type,
        audit_action=audit_action,
        old_confidence=old_confidence,
        old_status=old_status,
        create_review_task=create_review_task,
    )
    await _delete_memory_from_search_if_inactive(memory)
    await db.commit()
    await db.refresh(memory)
    return memory
