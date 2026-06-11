from datetime import datetime
from typing import Optional, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AIArtifactCreate(BaseModel):
    filename: str
    mime_type: str


class AIArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    mime_type: str
    storage_uri: str
    sha256: Optional[str]
    extraction_status: Optional[str] = None
    extraction_source: Optional[str] = None
    extraction_metadata_json: Optional[dict] = None
    extraction_error: Optional[str] = None
    created_at: datetime


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
