import os
import uuid
import logging
import httpx
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List, Any

from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import AIChatSession, AIChatMessage, AIChatArtifact, AIChatJob, AIConnectedAccount
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")


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

    # 1. Verify session ownership
    sess_res = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == user_id
        )
    )
    session = sess_res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    # 2. Save User Message
    user_msg = AIChatMessage(
        id=uuid.uuid4(),
        chat_session_id=session_id,
        user_id=user_id,
        role="user",
        content=req.content,
        created_at=datetime.utcnow()
    )
    db.add(user_msg)

    # Detect feedback from natural language
    content_clean = req.content.strip().lower()
    positive_feedback_keywords = ["that worked", "thanks, fixed", "yes that's right", "that solved it", "perfect, remember that", "it worked"]
    negative_feedback_keywords = ["no that's wrong", "that is outdated", "don't use that anymore", "that no longer applies", "forget that", "that didn't work"]

    is_positive = any(kw in content_clean for kw in positive_feedback_keywords)
    is_negative = any(kw in content_clean for kw in negative_feedback_keywords)

    if is_positive or is_negative:
        # Load the last assistant message of the session
        last_assistant_q = await db.execute(
            select(AIChatMessage).where(
                AIChatMessage.chat_session_id == session_id,
                AIChatMessage.role == "assistant"
            ).order_by(AIChatMessage.created_at.desc()).limit(1)
        )
        last_assistant = last_assistant_q.scalar_one_or_none()
        if last_assistant and last_assistant.metadata_json:
            context_data = last_assistant.metadata_json.get("context", {})
            injected = context_data.get("memories_injected", [])
            if injected:
                from app.models.models import AIMemory, AIMemoryUsageEvent, AITask
                for mem_ref in injected:
                    try:
                        mem_id = UUID(mem_ref["id"])
                        mem_q = await db.execute(select(AIMemory).where(AIMemory.id == mem_id))
                        memory = mem_q.scalar_one_or_none()
                        if memory:
                            f_type = "worked" if is_positive else "wrong"
                            old_confidence = memory.confidence
                            old_status = memory.status

                            if is_positive:
                                memory.success_count = (memory.success_count or 0) + 1
                                memory.last_confirmed_at = datetime.utcnow()
                                if memory.confidence == "low":
                                    memory.confidence = "medium"
                                elif memory.confidence == "medium":
                                    memory.confidence = "high"
                                audit_act = "memory_confidence_increased"
                            else:
                                memory.failure_count = (memory.failure_count or 0) + 1
                                if memory.confidence == "high":
                                    memory.confidence = "medium"
                                elif memory.confidence == "medium":
                                    memory.confidence = "low"
                                audit_act = "memory_confidence_decreased"

                                if (memory.failure_count or 0) > 3:
                                    memory.status = "needs_review"
                                    audit_act = "memory_flagged_for_review"
                                    task = AITask(
                                        id=uuid.uuid4(),
                                        title=f"Flagged by Natural Language: {memory.title}",
                                        description=f"Memory (id={memory.id}) has been flagged as 'wrong' via natural language feedback: '{req.content}'.",
                                        status="open",
                                        priority="high",
                                        linked_model="ai_memories",
                                        linked_record_id=str(memory.id),
                                    )
                                    db.add(task)

                            memory.updated_at = datetime.utcnow()

                            # Log Audit Event
                            audit_svc = AuditService(db)
                            await audit_svc.log_event(AIAuditEventCreate(
                                action_type=audit_act,
                                target_model="ai_memories",
                                target_record_id=str(memory.id),
                                actor_user_id=user_id,
                                input_summary=f"Natural language feedback detected: '{req.content}'. Confidence: {old_confidence} -> {memory.confidence}.",
                                risk_level="low",
                                status="success",
                            ))
                    except Exception as e:
                        logger.warning("Failed to apply natural language feedback to memory: %s", e)

    # Automatically update session title if it is still "New Chat"
    if session.title == "New Chat":
        session.title = req.content[:35] + ("..." if len(req.content) > 35 else "")

    # Link uploaded artifacts if provided
    for art_id in req.artifact_ids:
        chat_art = AIChatArtifact(
            id=uuid.uuid4(),
            chat_session_id=session_id,
            artifact_id=art_id,
            linked_message_id=user_msg.id
        )
        db.add(chat_art)

    # 3. Call Model Router with conversation history
    history_result = await db.execute(
        select(AIChatMessage).where(
            AIChatMessage.chat_session_id == session_id
        ).order_by(AIChatMessage.created_at.asc())
    )
    previous_messages = history_result.scalars().all()
    messages = [{"role": m.role, "content": m.content} for m in previous_messages if m.id != user_msg.id]
    messages.append({"role": "user", "content": req.content})

    from app.services.trace_service import TraceService
    trace_svc = TraceService(db, request_id=request_id)
    trace_svc.begin("chat_message", f"chat: {req.content[:60]}",
                    user_id=user_id, chat_session_id=session_id, message_id=user_msg.id)
    trace_svc.start_span("route_selection", "Route selected")
    context_size_span = trace_svc.start_span("context_loading", "Context loaded")

    try:
        from app.services.model_router import execute_chat, RouteNotFoundError, ProviderCallError
        trace_svc.start_span("model_request", "Model request")
        router_result = await execute_chat(
            db=db,
            messages=messages,
            task_type="general_chat",
            chat_session_id=session_id,
            user_id=user_id,
        )
        trace_svc.end_span("model_request", output_summary={
            "content_length": len(router_result.get("content", "")),
            "tool_call_count": router_result.get("tool_call_count", 0),
        })
    except RouteNotFoundError as e:
        await trace_svc.commit(status="failed", error_type="configuration_error", error_message=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "trace_id": trace_svc.trace_id,
                "error_type": "configuration_error",
                "error_message": str(e),
                "technical_detail": "RouteNotFoundError: " + str(e),
            },
        )
    except ProviderCallError as e:
        error_msg = str(e)
        await trace_svc.commit(status="failed", error_type="model_error", error_message=error_msg)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "trace_id": trace_svc.trace_id,
                "error_type": "model_error",
                "error_message": error_msg,
                "technical_detail": f"ProviderCallError (provider={e.provider}, model={e.model}): {error_msg}",
            },
        )
    except Exception as e:
        await trace_svc.commit(status="failed", error_type="server_error", error_message=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "trace_id": trace_svc.trace_id,
                "error_type": "server_error",
                "error_message": "Something went wrong while generating the response. Please try again.",
                "technical_detail": f"Unhandled exception: {e}",
            },
        )

    assistant_content = router_result.get("content", "")
    tool_calls_data = router_result.get("tool_calls")

    # Blank-response guard — execute_chat() already applies report fallback,
    # but catch any remaining blank content before the Reviewer runs
    if not assistant_content or not assistant_content.strip():
        if tool_calls_data:
            tool_errors = [
                t for t in tool_calls_data
                if isinstance(t.get("result"), dict) and t["result"].get("error")
            ]
            logger.warning(
                "Blank response after tool calls | request_id=%s user_id=%s session_id=%s tool_errors=%d",
                request_id, user_id, session_id, len(tool_errors),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "request_id": request_id,
                    "error_type": "empty_model_response_after_tools",
                    "error_message": "The model did not produce an answer after using tools.",
                    "technical_detail": {
                        "tool_calls": tool_calls_data,
                        "tool_errors": [
                            {
                                "tool_name": t.get("tool_name"),
                                "arguments": t.get("arguments"),
                                "error_type": t["result"].get("error_type", "unknown"),
                                "message": t["result"].get("message", str(t["result"])),
                            }
                            for t in tool_errors
                        ],
                    },
                },
            )
        logger.warning(
            "Blank response from model router | request_id=%s user_id=%s session_id=%s",
            request_id, user_id, session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "error_type": "server_error",
                "error_message": "The model returned an empty response. Please try again.",
                "technical_detail": "Model router returned blank content",
            },
        )

    # Reviewer check for finance/high-risk responses (runs after blank guard)
    reviewer_invoked = False
    reviewer_result_data = None
    reviewer_span = trace_svc.start_span("reviewer", "Reviewer check")
    try:
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        reviewer = ReviewerAgent()
        if reviewer._is_finance_question(req.content):
            reviewer_invoked = True
            review = await reviewer.review(
                ReviewRequest(
                    content=assistant_content,
                    user_question=req.content,
                    tool_results=tool_calls_data if tool_calls_data else None,
                )
            )
            reviewer_result_data = {
                "approved": review.approved,
                "risk_level": review.risk_level,
                "issues": review.issues,
                "required_changes": review.required_changes,
                "reviewer_notes": review.reviewer_notes,
            }
            if not review.approved:
                logger.warning(
                    "Reviewer rejected response | request_id=%s issues=%d risk=%s",
                    request_id, len(review.issues), review.risk_level,
                )
                trace_svc.end_span(reviewer_span, status="rejected",
                                    output_summary={"issues": review.issues, "risk_level": review.risk_level})
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

    model_provider = router_result.get("model_provider", "unknown")
    model_name = router_result.get("model_name", "unknown")
    token_usage = {
        "prompt_tokens": router_result.get("prompt_tokens", 0),
        "completion_tokens": router_result.get("completion_tokens", 0),
        "total_tokens": router_result.get("total_tokens", 0),
    }
    context_data = router_result.get("context")
    trace_svc.add_metadata(metadata={
        "model_provider": model_provider,
        "model_name": model_name,
        "prompt_tokens": token_usage["prompt_tokens"],
        "completion_tokens": token_usage["completion_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "tool_call_count": router_result.get("tool_call_count", 0),
        "reviewer_invoked": reviewer_invoked,
    })
    metadata_info: dict[str, Any] = {
        "technical_details": {
            "model_provider": model_provider,
            "model_name": model_name,
            "latency_ms": router_result.get("latency_ms", 0),
            "token_usage": token_usage,
            "request_id": request_id,
            "trace_id": trace_svc.trace_id,
            "reviewer_invoked": reviewer_invoked,
            "reviewer_result": reviewer_result_data,
            "tool_call_count": router_result.get("tool_call_count", 0),
        }
    }
    if tool_calls_data:
        metadata_info["technical_details"]["tool_calls"] = tool_calls_data
    if context_data:
        metadata_info["context"] = context_data

    # 4. Save Assistant Message (only on success)
    assistant_msg = AIChatMessage(
        id=uuid.uuid4(),
        chat_session_id=session_id,
        user_id=user_id,
        role="assistant",
        content=assistant_content,
        model_provider=model_provider,
        model_name=model_name,
        token_usage_json=token_usage,
        tool_call_json=tool_calls_data,
        metadata_json=metadata_info,
        created_at=datetime.utcnow()
    )
    db.add(assistant_msg)

    # Track memory usage and update last_used_at
    if context_data and context_data.get("memories_injected"):
        from app.models.models import AIMemoryUsageEvent, AIMemory
        for mem_ref in context_data["memories_injected"]:
            try:
                mem_id = UUID(mem_ref["id"])
                # 1. Update last_used_at on the memory record
                mem_q = await db.execute(select(AIMemory).where(AIMemory.id == mem_id))
                mem_record = mem_q.scalar_one_or_none()
                if mem_record:
                    mem_record.last_used_at = datetime.utcnow()

                # 2. Record individual usage event in database
                usage_event = AIMemoryUsageEvent(
                    id=uuid.uuid4(),
                    memory_id=mem_id,
                    chat_session_id=session_id,
                    chat_message_id=assistant_msg.id,
                    user_id=user_id,
                    request_id=request_id,
                    used_in_context="true",
                    used_in_final_answer="true" if mem_ref["title"].lower() in assistant_content.lower() else "false",
                    created_at=datetime.utcnow()
                )
                db.add(usage_event)
            except Exception as e:
                logger.warning("Failed to record memory usage event: %s", e)

    # Update session timestamps
    session.last_message_at = datetime.utcnow()
    session.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(assistant_msg)

    # Log audit event
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
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

    # Async memory extraction via Service Bus (with inline fallback)
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
    except Exception:
        # Fallback: inline extraction (original behavior)
        try:
            from app.services.memory import MemoryCandidateService
            memory_svc = MemoryCandidateService(db)
            candidates = await memory_svc.extract_from_messages(
                messages=[user_msg, assistant_msg],
                user_id=user_id,
            )
            if candidates:
                logger.info(
                    "Memory candidates found | session=%s count=%d types=%s",
                    session_id, len(candidates), [c.type for c in candidates],
                )
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
        except Exception as inner_exc:
            logger.warning("Inline memory extraction failed: %s", inner_exc)

    await trace_svc.commit(status="success")
    return assistant_msg
