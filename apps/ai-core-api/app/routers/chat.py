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
            AIChatSession.user_id == str(user_id),
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
            AIChatSession.user_id == str(user_id)
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
            AIChatSession.user_id == str(user_id)
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
            AIChatSession.user_id == str(user_id)
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
    sess_res = await db.execute(
        select(AIChatSession).where(
            AIChatSession.id == session_id,
            AIChatSession.user_id == str(user_id)
        )
    )
    if not sess_res.scalar_one_or_none():
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
            AIChatSession.user_id == str(user_id)
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

    try:
        from app.services.model_router import execute_chat, RouteNotFoundError, ProviderCallError
        router_result = await execute_chat(
            db=db,
            messages=messages,
            task_type="general_chat",
            chat_session_id=session_id,
            user_id=user_id,
        )
    except RouteNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "error_type": "configuration_error",
                "error_message": str(e),
                "technical_detail": "RouteNotFoundError: " + str(e),
            },
        )
    except ProviderCallError as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "error_type": "model_error",
                "error_message": error_msg,
                "technical_detail": f"ProviderCallError (provider={e.provider}, model={e.model}): {error_msg}",
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "request_id": request_id,
                "error_type": "server_error",
                "error_message": "Something went wrong while generating the response. Please try again.",
                "technical_detail": f"Unhandled exception: {e}",
            },
        )

    assistant_content = router_result.get("content", "")
    tool_calls_data = router_result.get("tool_calls")

    # Reviewer check for finance/high-risk responses
    try:
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        reviewer = ReviewerAgent()
        review = await reviewer.review(
            ReviewRequest(
                content=assistant_content,
                user_question=req.content,
                tool_results=tool_calls_data if tool_calls_data else None,
            )
        )
        if not review.approved:
            logger.warning(
                "Reviewer rejected response | request_id=%s issues=%d risk=%s",
                request_id, len(review.issues), review.risk_level,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "request_id": request_id,
                    "error_type": "review_failed",
                    "error_message": "The response was reviewed and rejected. Please try again.",
                    "technical_detail": f"Review issues: {'; '.join(review.issues)}",
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Reviewer check failed (non-blocking): %s", exc)

    # Blank-response guard
    if not assistant_content or not assistant_content.strip():
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

    model_provider = router_result.get("model_provider", "unknown")
    model_name = router_result.get("model_name", "unknown")
    token_usage = {
        "prompt_tokens": router_result.get("prompt_tokens", 0),
        "completion_tokens": router_result.get("completion_tokens", 0),
        "total_tokens": router_result.get("total_tokens", 0),
    }
    context_data = router_result.get("context")
    metadata_info: dict[str, Any] = {
        "technical_details": {
            "model_provider": model_provider,
            "model_name": model_name,
            "latency_ms": router_result.get("latency_ms", 0),
            "token_usage": token_usage,
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

    return assistant_msg
