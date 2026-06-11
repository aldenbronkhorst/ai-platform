from datetime import datetime
from typing import Optional, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict


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
    save_mode: str = "auto"  # auto or confirm
