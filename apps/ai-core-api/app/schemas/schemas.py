from datetime import datetime
from typing import Optional, List, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    version: str
    dependencies: dict


class AIUserCreate(BaseModel):
    email: str
    display_name: Optional[str] = None
    role: str = "user"
    department: Optional[str] = None


class AIUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: Optional[str]
    role: str
    department: Optional[str]
    created_at: datetime


class AIArtifactCreate(BaseModel):
    job_id: Optional[UUID] = None
    artifact_type: str
    filename: str
    mime_type: str
    source_tool: Optional[str] = None
    stage: Optional[str] = None
    retention_policy: str = "standard"


class AIArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_id: Optional[UUID]
    artifact_type: str
    filename: str
    mime_type: str
    storage_uri: str
    sha256: Optional[str]
    source_tool: Optional[str]
    stage: Optional[str]
    extraction_status: Optional[str] = None
    extraction_source: Optional[str] = None
    extraction_metadata_json: Optional[dict] = None
    extraction_error: Optional[str] = None
    created_at: datetime


class AIToolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    display_name: str
    description: Optional[str]
    target_system: str
    version: str
    status: str
    requires_approval: str
    created_at: datetime


class AIAuditEventCreate(BaseModel):
    actor_type: str = "user"
    actor_user_id: Optional[UUID] = None
    identity_mode: str = "user-delegated"
    interface: Optional[str] = None
    action_type: str
    tool_name: Optional[str] = None
    target_system: Optional[str] = None
    target_model: Optional[str] = None
    target_record_id: Optional[str] = None
    job_id: Optional[UUID] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    risk_level: str = "low"
    status: str = "success"
    cost_estimate: Optional[float] = None


class ContextRequest(BaseModel):
    task: Optional[str] = None
    systems: Optional[List[str]] = None
    record_model: Optional[str] = None
    supplier: Optional[str] = None
    customer: Optional[str] = None
    department: Optional[str] = None
    workflow: Optional[str] = None
    limit: int = 10


class AIMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    type: str
    title: str
    summary: Optional[str]
    body: Optional[str]
    scope_type: Optional[str]
    scope_value: Optional[str]
    entities_json: Optional[Any]
    source_type: Optional[str]
    source_id: Optional[str]
    conversation_id: Optional[UUID]
    message_id: Optional[UUID]
    confidence: str
    risk_level: str
    status: str
    priority: int
    success_count: int
    failure_count: int
    last_used_at: Optional[datetime]
    last_confirmed_at: Optional[datetime]
    version: int
    created_by_user_id: Optional[UUID]
    approved_by_user_id: Optional[UUID]
    metadata_json: Optional[Any]
    created_at: datetime
    updated_at: datetime


class MemoryCandidate(BaseModel):
    type: str
    title: str
    summary: Optional[str] = None
    body: Optional[str] = None
    scope_type: Optional[str] = None
    scope_value: Optional[str] = None
    entities_json: Optional[Any] = None
    confidence: str = "medium"
    risk_level: str = "low"
    save_mode: str = "auto"  # auto, confirm, admin_approval


class MemoryFeedbackRequest(BaseModel):
    feedback_type: str  # helpful, worked, wrong, outdated, not_relevant, do_not_use, needs_review
    comment: Optional[str] = None
    chat_message_id: Optional[UUID] = None
