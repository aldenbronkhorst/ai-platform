from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import dev_api_key_auth
from app.services.artifact import ArtifactService
from app.services.audit import AuditService
from app.schemas.schemas import AIArtifactCreate, AIArtifactResponse, AIAuditEventCreate
from uuid import UUID

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("", response_model=AIArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    artifact_type: str = Form(...),
    filename: str = Form(...),
    mime_type: str = Form(...),
    job_id: UUID = Form(None),
    source_tool: str = Form(None),
    stage: str = Form(None),
    retention_policy: str = Form("standard"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    auth=Depends(dev_api_key_auth),
):
    svc = ArtifactService(db)
    data = AIArtifactCreate(
        job_id=job_id,
        artifact_type=artifact_type,
        filename=filename,
        mime_type=mime_type,
        source_tool=source_tool,
        stage=stage,
        retention_policy=retention_policy,
    )
    content = await file.read()
    artifact = await svc.upload(data, content, created_by_user_id=auth.get("user_id"))

    # Audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="create",
        target_system="ai-platform",
        target_model="ai_artifacts",
        target_record_id=str(artifact.id),
        job_id=job_id,
        input_summary=f"Uploaded artifact {filename} type={artifact_type} stage={stage}",
        risk_level="low",
        status="success",
    ))

    return artifact


@router.get("/{artifact_id}", response_model=AIArtifactResponse)
async def get_artifact(
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(dev_api_key_auth),
):
    svc = ArtifactService(db)
    artifact = await svc.get_by_id(artifact_id)
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return artifact
