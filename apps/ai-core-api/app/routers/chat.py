import uuid
import logging
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Any

from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import (
    AIChatSession, AIChatMessage, AIChatArtifact, AIMemory, AIMemoryUsageEvent, AITask,
)
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatSessionCreate(BaseModel):
    title: Optional[str] = Field("New Chat", description="Optional initial title")
    workflow_context: Optional[str] = Field(None, description="Active business workflow context")


class ChatSessionUpdate(BaseModel):
    title: str


class ChatMessageCreate(BaseModel):
    content: str
    artifact_ids: Optional[List[UUID]] = Field(default_factory=list)
    workflow_context: Optional[str] = None


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    chat_session_id: UUID
    user_id: UUID
    role: str
    content: str
    created_at: datetime
    model_name: Optional[str] = None
    model_provider: Optional[str] = None
    token_usage_json: Optional[Any] = None
    tool_call_json: Optional[Any] = None
    metadata_json: Optional[Any] = None


class ChatSessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    status: str
    workflow_context: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    last_message_at: Optional[datetime]


@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    req: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Creates a new, independent chat session for the authenticated user."""
    user_id = auth["user_id"]
    
    session = AIChatSession(
        id=uuid.uuid4(),
        user_id=user_id,
        title=req.title or "New Chat",
        status="active",
        workflow_context=req.workflow_context,
        last_message_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/sessions", response_model=List[ChatSessionResponse])
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Lists all active chat sessions for the authenticated user, sorted by last_message_at desc."""
    user_id = auth["user_id"]
    result = await db.execute(
        select(AIChatSession).where(
            AIChatSession.user_id == user_id,
            AIChatSession.status == "active"
        ).order_by(AIChatSession.last_message_at.desc())
    )
    return result.scalars().all()


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Gets details of a specific chat session. Enforces user isolation."""
    user_id = auth["user_id"]
    result = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return session


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_chat_session(
    session_id: UUID,
    req: ChatSessionUpdate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Renames or updates a chat session title."""
    user_id = auth["user_id"]
    result = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    
    session.title = req.title
    session.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Soft-deletes/archives a chat session."""
    user_id = auth["user_id"]
    result = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    
    session.status = "archived"
    session.updated_at = datetime.utcnow()
    await db.commit()


@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageResponse])
async def list_chat_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Returns the message history for a specific chat session."""
    user_id = auth["user_id"]
    
    # Verify session ownership
    sess_result = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == user_id
        )
    )
    session = sess_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    result = await db.execute(
        select(AIChatMessage).where(
            AIChatMessage.chat_session_id == session_id
        ).order_by(AIChatMessage.created_at.asc())
    )
    return result.scalars().all()


POSITIVE_FEEDBACK_KEYWORDS = [
    "that worked", "thanks, fixed", "yes that's right", "that solved it",
    "perfect, remember that", "it worked",
]
NEGATIVE_FEEDBACK_KEYWORDS = [
    "no that's wrong", "that is outdated", "don't use that anymore",
    "that no longer applies", "forget that", "that didn't work",
]


async def _get_owned_session(db: AsyncSession, session_id: UUID, user_id: UUID) -> AIChatSession:
    result = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == user_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return session


def _feedback_kind(content: str) -> str | None:
    clean = content.strip().lower()
    if any(keyword in clean for keyword in POSITIVE_FEEDBACK_KEYWORDS):
        return "worked"
    if any(keyword in clean for keyword in NEGATIVE_FEEDBACK_KEYWORDS):
        return "wrong"
    return None


async def _last_assistant_message(db: AsyncSession, session_id: UUID) -> AIChatMessage | None:
    result = await db.execute(
        select(AIChatMessage).where(
            AIChatMessage.chat_session_id == session_id,
            AIChatMessage.role == "assistant",
        ).order_by(AIChatMessage.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


def _adjust_memory_confidence(memory: AIMemory, feedback_kind: str) -> str:
    if feedback_kind == "worked":
        memory.success_count = (memory.success_count or 0) + 1
        memory.last_confirmed_at = datetime.utcnow()
        if memory.confidence == "low":
            memory.confidence = "medium"
        elif memory.confidence == "medium":
            memory.confidence = "high"
        return "memory_confidence_increased"

    memory.failure_count = (memory.failure_count or 0) + 1
    if memory.confidence == "high":
        memory.confidence = "medium"
    elif memory.confidence == "medium":
        memory.confidence = "low"
    if (memory.failure_count or 0) > 3:
        memory.status = "needs_review"
        return "memory_flagged_for_review"
    return "memory_confidence_decreased"


def _add_memory_review_task(db: AsyncSession, memory: AIMemory, content: str) -> None:
    db.add(AITask(
        id=uuid.uuid4(),
        title=f"Flagged by Natural Language: {memory.title}",
        description=f"Memory (id={memory.id}) has been flagged as 'wrong' via natural language feedback: '{content}'.",
        status="open",
        priority="high",
        linked_model="ai_memories",
        linked_record_id=str(memory.id),
    ))


async def _apply_memory_feedback(
    db: AsyncSession,
    memory_id: UUID,
    feedback_kind: str,
    user_id: UUID,
    content: str,
) -> None:
    mem_q = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    memory = mem_q.scalar_one_or_none()
    if not memory:
        return

    old_confidence = memory.confidence
    audit_action = _adjust_memory_confidence(memory, feedback_kind)
    if audit_action == "memory_flagged_for_review":
        _add_memory_review_task(db, memory, content)
    memory.updated_at = datetime.utcnow()

    await AuditService(db).log_event(AIAuditEventCreate(
        action_type=audit_action,
        target_model="ai_memories",
        target_record_id=str(memory.id),
        actor_user_id=user_id,
        input_summary=f"Natural language feedback detected: '{content}'. Confidence: {old_confidence} -> {memory.confidence}.",
        risk_level="low",
        status="success",
    ))


async def _apply_natural_language_feedback(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    content: str,
) -> None:
    feedback_kind = _feedback_kind(content)
    if not feedback_kind:
        return

    last_assistant = await _last_assistant_message(db, session_id)
    if not last_assistant or not last_assistant.metadata_json:
        return

    injected = last_assistant.metadata_json.get("context", {}).get("memories_injected", [])
    for memory_ref in injected:
        try:
            await _apply_memory_feedback(db, UUID(memory_ref["id"]), feedback_kind, user_id, content)
        except Exception as exc:
            logger.warning("Failed to apply natural language feedback to memory: %s", exc)


def _new_chat_message(session_id: UUID, user_id: UUID, role: str, content: str, **extra: Any) -> AIChatMessage:
    return AIChatMessage(
        id=uuid.uuid4(),
        chat_session_id=session_id,
        user_id=user_id,
        role=role,
        content=content,
        created_at=datetime.utcnow(),
        **extra,
    )


async def _persist_user_message(db: AsyncSession, session_id: UUID, user_id: UUID, content: str) -> AIChatMessage:
    message = _new_chat_message(session_id, user_id, "user", content)
    db.add(message)
    await db.flush()
    return message


def _update_session_title(session: AIChatSession, content: str) -> None:
    if session.title == "New Chat":
        session.title = content[:35] + ("..." if len(content) > 35 else "")


def _link_chat_artifacts(db: AsyncSession, session_id: UUID, message_id: UUID, artifact_ids: list[UUID]) -> None:
    for artifact_id in artifact_ids:
        db.add(AIChatArtifact(
            id=uuid.uuid4(),
            chat_session_id=session_id,
            artifact_id=artifact_id,
            linked_message_id=message_id,
        ))


async def _conversation_messages(db: AsyncSession, session_id: UUID, user_msg: AIChatMessage, content: str) -> list[dict[str, str]]:
    history = await db.execute(
        select(AIChatMessage).where(
            AIChatMessage.chat_session_id == session_id
        ).order_by(AIChatMessage.created_at.asc())
    )
    messages = [{"role": msg.role, "content": msg.content} for msg in history.scalars().all() if msg.id != user_msg.id]
    messages.append({"role": "user", "content": content})
    return messages


def _failed_assistant_message(session_id: UUID, user_id: UUID, error_type: str, error_message: str, request_id: str, trace_id: str) -> AIChatMessage:
    return _new_chat_message(
        session_id,
        user_id,
        "assistant",
        "",
        metadata_json={
            "failed": True,
            "error_type": error_type,
            "error_message": error_message,
            "request_id": request_id,
            "trace_id": trace_id,
        },
    )


async def _persist_failed_message(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    error_type: str,
    error_message: str,
    request_id: str,
    trace_id: str,
) -> None:
    db.add(_failed_assistant_message(session_id, user_id, error_type, error_message, request_id, trace_id))
    await db.commit()


async def _run_model_router(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    user_msg: AIChatMessage,
    content: str,
    messages: list[dict[str, str]],
    request_id: str,
):
    from app.services.trace_service import TraceService
    from app.services.model_router import execute_chat, RouteNotFoundError, ProviderCallError

    trace_svc = TraceService(db, request_id=request_id)
    trace_svc.begin("chat_message", f"chat: {content[:60]}", user_id=user_id, chat_session_id=session_id, message_id=user_msg.id)
    try:
        model_span = trace_svc.start_span("model_request", "Model request")
        router_result = await execute_chat(
            db=db,
            messages=messages,
            task_type="general_chat",
            chat_session_id=session_id,
            user_id=user_id,
        )
        trace_svc.end_span(model_span, output_summary={
            "content_length": len(router_result.get("content", "")),
            "tool_call_count": router_result.get("tool_call_count", 0),
        })
        return router_result, trace_svc
    except RouteNotFoundError as exc:
        await trace_svc.commit(status="failed", error_type="configuration_error", error_message=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "trace_id": trace_svc.trace_id,
                "error_type": "configuration_error",
                "error_message": str(exc),
                "technical_detail": "RouteNotFoundError: " + str(exc),
            },
        )
    except ProviderCallError as exc:
        error_msg = str(exc)
        await _persist_failed_message(db, session_id, user_id, "model_error", error_msg, request_id, trace_svc.trace_id)
        await trace_svc.commit(status="failed", error_type="model_error", error_message=error_msg)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "trace_id": trace_svc.trace_id,
                "error_type": "model_error",
                "error_message": error_msg,
                "technical_detail": f"ProviderCallError (provider={exc.provider}, model={exc.model}): {error_msg}",
            },
        )
    except Exception as exc:
        error_msg = str(exc)
        await _persist_failed_message(db, session_id, user_id, "server_error", error_msg, request_id, trace_svc.trace_id)
        await trace_svc.commit(status="failed", error_type="server_error", error_message=error_msg)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "trace_id": trace_svc.trace_id,
                "error_type": "server_error",
                "error_message": "Something went wrong while generating the response. Please try again.",
                "technical_detail": f"Unhandled exception: {exc}",
            },
        )


def _blank_tool_error_details(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors = [item for item in tool_calls if isinstance(item.get("result"), dict) and item["result"].get("error")]
    return [
        {
            "tool_name": item.get("tool_name"),
            "arguments": item.get("arguments"),
            "error_type": item["result"].get("error_type", "unknown"),
            "message": item["result"].get("message", str(item["result"])),
        }
        for item in errors
    ]


def _raise_on_blank_response(router_result: dict[str, Any], request_id: str, user_id: UUID, session_id: UUID) -> None:
    assistant_content = router_result.get("content", "")
    if assistant_content and assistant_content.strip():
        return

    tool_calls = router_result.get("tool_calls")
    if tool_calls:
        tool_error_details = _blank_tool_error_details(tool_calls)
        logger.warning(
            "Blank response after tool calls | request_id=%s user_id=%s session_id=%s tool_errors=%d",
            request_id, user_id, session_id, len(tool_error_details),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "error_type": "empty_model_response_after_tools",
                "error_message": "The model did not produce an answer after using tools.",
                "technical_detail": {"tool_calls": tool_calls, "tool_errors": tool_error_details},
            },
        )

    logger.warning("Blank response from model router | request_id=%s user_id=%s session_id=%s", request_id, user_id, session_id)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "request_id": request_id,
            "error_type": "server_error",
            "error_message": "The model returned an empty response. Please try again.",
            "technical_detail": "Model router returned blank content",
        },
    )


async def _review_router_result(
    content: str,
    assistant_content: str,
    tool_calls: Any,
    trace_svc: Any,
    request_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    reviewer_invoked = False
    reviewer_result_data = None
    reviewer_span = trace_svc.start_span("reviewer", "Reviewer check")
    try:
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest

        reviewer = ReviewerAgent()
        if reviewer._is_finance_question(content):
            reviewer_invoked = True
            review = await reviewer.review(ReviewRequest(
                content=assistant_content,
                user_question=content,
                tool_results=tool_calls if tool_calls else None,
            ))
            reviewer_result_data = {
                "approved": review.approved,
                "risk_level": review.risk_level,
                "issues": review.issues,
                "required_changes": review.required_changes,
                "reviewer_notes": review.reviewer_notes,
            }
            if not review.approved:
                logger.warning("Reviewer rejected response | request_id=%s issues=%d risk=%s", request_id, len(review.issues), review.risk_level)
                trace_svc.end_span(reviewer_span, status="rejected", output_summary={"issues": review.issues, "risk_level": review.risk_level})
                await trace_svc.commit(status="failed", error_type="review_failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "request_id": request_id,
                        "error_type": "review_failed",
                        "error_message": "The response was reviewed and rejected. Please try again.",
                        "technical_detail": f"Review issues: {'; '.join(review.issues)}",
                        "reviewer_result": {
                            "approved": review.approved,
                            "issues": review.issues,
                            "risk_level": review.risk_level,
                            "reviewer_notes": review.reviewer_notes,
                        },
                    },
                )
    except HTTPException:
        await trace_svc.commit(status="failed", error_type="review_failed")
        raise
    except Exception as exc:
        logger.warning("Reviewer check failed (non-blocking): %s", exc)
    else:
        trace_svc.end_span(reviewer_span, output_summary=reviewer_result_data)
    return reviewer_invoked, reviewer_result_data


def _token_usage(router_result: dict[str, Any]) -> dict[str, int]:
    return {
        "prompt_tokens": router_result.get("prompt_tokens", 0),
        "completion_tokens": router_result.get("completion_tokens", 0),
        "total_tokens": router_result.get("total_tokens", 0),
    }


def _assistant_metadata(
    router_result: dict[str, Any],
    request_id: str,
    trace_id: str,
    reviewer_invoked: bool,
    reviewer_result_data: dict[str, Any] | None,
) -> dict[str, Any]:
    token_usage = _token_usage(router_result)
    metadata = {
        "technical_details": {
            "model_provider": router_result.get("model_provider", "unknown"),
            "model_name": router_result.get("model_name", "unknown"),
            "latency_ms": router_result.get("latency_ms", 0),
            "token_usage": token_usage,
            "request_id": request_id,
            "trace_id": trace_id,
            "reviewer_invoked": reviewer_invoked,
            "reviewer_result": reviewer_result_data,
            "tool_call_count": router_result.get("tool_call_count", 0),
        }
    }
    if router_result.get("tool_calls"):
        metadata["technical_details"]["tool_calls"] = router_result["tool_calls"]
    if router_result.get("context"):
        metadata["context"] = router_result["context"]
    return metadata


def _build_assistant_message(
    session_id: UUID,
    user_id: UUID,
    router_result: dict[str, Any],
    request_id: str,
    trace_id: str,
    reviewer_invoked: bool,
    reviewer_result_data: dict[str, Any] | None,
) -> AIChatMessage:
    return _new_chat_message(
        session_id,
        user_id,
        "assistant",
        router_result.get("content", ""),
        model_provider=router_result.get("model_provider", "unknown"),
        model_name=router_result.get("model_name", "unknown"),
        token_usage_json=_token_usage(router_result),
        tool_call_json=router_result.get("tool_calls"),
        metadata_json=_assistant_metadata(router_result, request_id, trace_id, reviewer_invoked, reviewer_result_data),
    )


async def _record_memory_usage(
    db: AsyncSession,
    context_data: dict[str, Any] | None,
    assistant_msg: AIChatMessage,
    session_id: UUID,
    user_id: UUID,
    request_id: str,
) -> None:
    if not context_data or not context_data.get("memories_injected"):
        return
    for memory_ref in context_data["memories_injected"]:
        try:
            mem_id = UUID(memory_ref["id"])
            mem_q = await db.execute(select(AIMemory).where(AIMemory.id == mem_id))
            memory = mem_q.scalar_one_or_none()
            if memory:
                memory.last_used_at = datetime.utcnow()
            db.add(AIMemoryUsageEvent(
                id=uuid.uuid4(),
                memory_id=mem_id,
                chat_session_id=session_id,
                chat_message_id=assistant_msg.id,
                user_id=user_id,
                request_id=request_id,
                used_in_context="true",
                used_in_final_answer="true" if memory_ref["title"].lower() in assistant_msg.content.lower() else "false",
                created_at=datetime.utcnow(),
            ))
        except Exception as exc:
            logger.warning("Failed to record memory usage event: %s", exc)


async def _persist_success(
    db: AsyncSession,
    session: AIChatSession,
    assistant_msg: AIChatMessage,
    session_id: UUID,
    user_id: UUID,
    request_id: str,
    context_data: dict[str, Any] | None,
) -> None:
    db.add(assistant_msg)
    await _record_memory_usage(db, context_data, assistant_msg, session_id, user_id, request_id)
    session.last_message_at = datetime.utcnow()
    session.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(assistant_msg)

    await AuditService(db).log_event(AIAuditEventCreate(
        action_type="chat_message",
        target_system="ai-platform",
        target_model="ai_chat_messages",
        target_record_id=str(assistant_msg.id),
        actor_user_id=user_id,
        input_summary=f"Sent chat message in session {session_id}",
        risk_level="low",
        status="success",
    ))
    await db.commit()


async def _enqueue_or_extract_memories(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    user_msg: AIChatMessage,
    assistant_msg: AIChatMessage,
) -> None:
    try:
        from app.services.service_bus import send_message_async, QUEUE_MEMORY_EXTRACTION
        sent = await send_message_async(QUEUE_MEMORY_EXTRACTION, {
            "message_type": "memory_extraction",
            "conversation_id": str(session_id),
            "user_id": str(user_id),
        })
        if not sent:
            raise RuntimeError("Service Bus not configured")
        logger.info("Enqueued memory extraction | session=%s", session_id)
        return
    except Exception:
        pass

    try:
        from app.services.memory import MemoryCandidateService
        memory_svc = MemoryCandidateService(db)
        candidates = await memory_svc.extract_from_messages(messages=[user_msg, assistant_msg], user_id=user_id)
        if candidates:
            logger.info("Memory candidates found | session=%s count=%d types=%s", session_id, len(candidates), [c.type for c in candidates])
        for candidate in candidates:
            is_dup = await memory_svc.check_duplicate(candidate)
            if not is_dup and candidate.save_mode == "auto":
                saved = await memory_svc.save_candidate(
                    candidate=candidate,
                    user_id=user_id,
                    conversation_id=session_id,
                    message_id=assistant_msg.id,
                )
                logger.info("Auto-saved memory | id=%s type=%s", saved.id, candidate.type)
    except Exception as exc:
        logger.warning("Inline memory extraction failed: %s", exc)


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def post_chat_message(
    session_id: UUID,
    req: ChatMessageCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Posts a message to the chat session and executes the platform business assistant flow.

    Returns a natural language response with technical logs safely hidden inside metadata_json.
    On failure, returns a structured JSON error response — failed messages are not persisted.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response.headers["X-Request-ID"] = request_id
    user_id = auth["user_id"]
    session = await _get_owned_session(db, session_id, user_id)
    user_msg = await _persist_user_message(db, session_id, user_id, req.content)
    await _apply_natural_language_feedback(db, session_id, user_id, req.content)
    _update_session_title(session, req.content)
    _link_chat_artifacts(db, session_id, user_msg.id, req.artifact_ids or [])

    messages = await _conversation_messages(db, session_id, user_msg, req.content)
    router_result, trace_svc = await _run_model_router(db, session_id, user_id, user_msg, req.content, messages, request_id)
    _raise_on_blank_response(router_result, request_id, user_id, session_id)

    assistant_content = router_result.get("content", "")
    tool_calls_data = router_result.get("tool_calls")
    reviewer_invoked, reviewer_result_data = await _review_router_result(
        req.content, assistant_content, tool_calls_data, trace_svc, request_id,
    )
    context_data = router_result.get("context")
    token_usage = _token_usage(router_result)
    trace_svc.add_metadata(metadata={
        "model_provider": router_result.get("model_provider", "unknown"),
        "model_name": router_result.get("model_name", "unknown"),
        "prompt_tokens": token_usage["prompt_tokens"],
        "completion_tokens": token_usage["completion_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "tool_call_count": router_result.get("tool_call_count", 0),
        "reviewer_invoked": reviewer_invoked,
    })

    assistant_msg = _build_assistant_message(
        session_id, user_id, router_result, request_id, trace_svc.trace_id,
        reviewer_invoked, reviewer_result_data,
    )
    await _persist_success(db, session, assistant_msg, session_id, user_id, request_id, context_data)
    await _enqueue_or_extract_memories(db, session_id, user_id, user_msg, assistant_msg)
    await trace_svc.commit(status="success")
    return assistant_msg
