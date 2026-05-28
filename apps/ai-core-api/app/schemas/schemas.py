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


class AIJobCreate(BaseModel):
    workflow_type: Optional[str] = None
    title: str
    linked_system: Optional[str] = None
    linked_model: Optional[str] = None
    linked_record_id: Optional[str] = None
    identity_mode: str = "user-delegated"


class AIJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_type: Optional[str]
    title: str
    status: str
    requested_by_user_id: Optional[UUID]
    identity_mode: str
    linked_system: Optional[str]
    linked_model: Optional[str]
    linked_record_id: Optional[str]
    current_step: Optional[str]
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]


class AITaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    owner_user_id: Optional[UUID] = None
    department: Optional[str] = None
    linked_system: Optional[str] = None
    linked_model: Optional[str] = None
    linked_record_id: Optional[str] = None
    due_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None


class AITaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    description: Optional[str]
    status: str
    priority: str
    owner_user_id: Optional[UUID]
    department: Optional[str]
    linked_system: Optional[str]
    linked_model: Optional[str]
    linked_record_id: Optional[str]
    next_review_at: Optional[datetime]
    due_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]


class AITaskUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    next_review_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    current_step: Optional[str] = None


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
    created_at: datetime


class AIToolCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    target_system: str
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    version: str = "1.0.0"
    requires_approval: str = "false"


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


class AIAuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    timestamp: datetime
    actor_type: str
    actor_user_id: Optional[UUID]
    identity_mode: str
    interface: Optional[str]
    action_type: str
    tool_name: Optional[str]
    target_system: Optional[str]
    target_model: Optional[str]
    target_record_id: Optional[str]
    job_id: Optional[UUID]
    input_summary: Optional[str]
    output_summary: Optional[str]
    risk_level: str
    status: str
    cost_estimate: Optional[float]


class AIRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    body: str
    scope_type: Optional[str]
    scope_value: Optional[str]
    department: Optional[str]
    workflow: Optional[str]
    supplier: Optional[str]
    customer: Optional[str]
    status: str
    priority: int
    effective_from: datetime
    effective_to: Optional[datetime]
    version: int


class AICompanyFactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    key: str
    value: str
    category: Optional[str]
    source: Optional[str]
    confidence: str


class ContextRequest(BaseModel):
    task: Optional[str] = None
    systems: Optional[List[str]] = None
    record_model: Optional[str] = None
    supplier: Optional[str] = None
    customer: Optional[str] = None
    department: Optional[str] = None
    workflow: Optional[str] = None
    limit: int = 10


class ContextResponse(BaseModel):
    rules: List[AIRuleResponse]
    facts: List[AICompanyFactResponse]
    tools: List[AIToolResponse]
