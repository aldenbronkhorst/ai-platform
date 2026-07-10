from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AIArtifactCreate(BaseModel):
    filename: str
    mime_type: str


class AIArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    artifact_type: str
    filename: str
    mime_type: str
    storage_uri: str
    sha256: Optional[str]
    extraction_status: Optional[str] = None
    extraction_source: Optional[str] = None
    extraction_metadata_json: Optional[dict] = None
    extraction_error: Optional[str] = None
    created_at: datetime
