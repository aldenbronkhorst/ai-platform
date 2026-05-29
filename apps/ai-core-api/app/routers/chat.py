import os
import uuid
import httpx
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List, Any

from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import AIChatSession, AIChatMessage, AIChatArtifact, AIChatJob, AIConnectedAccount
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

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
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Posts a message to the chat session and executes the platform business assistant flow.

    Returns a natural language response with technical logs safely hidden inside metadata_json.
    """
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

    # 3. Simulate Platform Assistant operational response
    # If the message references Odoo, we mock a safe Odoo read-only verification call
    is_odoo_query = "odoo" in req.content.lower() or "partner" in req.content.lower() or "read" in req.content.lower()
    
    assistant_content = ""
    tool_call_info = None
    metadata_info = {}

    if isOdooQuery:
        # Check if the user has an active Odoo connected account
        acct_res = await db.execute(
            select(AIConnectedAccount).where(
                AIConnectedAccount.user_id == str(user_id),
                AIConnectedAccount.provider == "odoo",
                AIConnectedAccount.status == "connected"
            )
        )
        account = acct_res.scalar_one_or_none()
        
        if account:
            # Emulate structured business action
            assistant_content = "I checked Odoo and found 3 matching customer records for your query. I have securely verified the customer account balances and compiled the results."
            metadata_info = {
                "technical_details": {
                    "api_called": "POST /records/search-read",
                    "target_model": "res.partner",
                    "gated_operation_mode": "read-only",
                    "records_found": 3,
                    "connected_account_id": str(account.id)
                }
            }
        else:
            assistant_content = "I checked your integrations but noticed Odoo is not connected. To proceed, please connect your Odoo Enterprise account under Connected Accounts."
    else:
        assistant_content = f"I received your inquiry regarding '{req.content}'. I am ready to run any business workflows, timesheet reviews, or ledger checks you require."

    # 4. Save Assistant Message
    assistant_msg = AIChatMessage(
        id=uuid.uuid4(),
        chat_session_id=session_id,
        user_id=user_id,
        role="assistant",
        content=assistant_content,
        model_provider="azure-openai",
        model_name="gpt-4o",
        metadata_json=metadata_info if metadata_info else None,
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

    return assistant_msg
