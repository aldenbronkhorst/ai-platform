import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.artifact import ArtifactService
from app.services.audit import AuditService
from app.schemas.schemas import AIArtifactCreate, AIArtifactResponse, AIAuditEventCreate
from uuid import UUID

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
logger = logging.getLogger(__name__)


def _can_read_artifact(auth: dict, artifact) -> bool:
    user_id = auth.get("user_id")
    return artifact.created_by_user_id == user_id or "AIPlatform.Admin" in set(auth.get("roles", []))


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
    auth=Depends(api_key_auth),
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
    try:
        artifact = await svc.upload(data, content, created_by_user_id=auth.get("user_id"))
    except Exception as exc:
        logger.exception("Artifact upload failed | filename=%s artifact_type=%s", filename, artifact_type)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "artifact_upload_failed",
                "error_message": "The file could not be uploaded to artifact storage.",
                "technical_detail": str(exc),
            },
        ) from exc

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


@router.get("", response_model=list[AIArtifactResponse])
async def list_artifacts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ArtifactService(db)
    return await svc.list_for_user(auth.get("user_id"), limit=limit, offset=offset)


@router.get("/{artifact_id}", response_model=AIArtifactResponse)
async def get_artifact(
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ArtifactService(db)
    artifact = await svc.get_by_id(artifact_id)
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if not _can_read_artifact(auth, artifact):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return artifact


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ArtifactService(db)
    artifact = await svc.get_by_id(artifact_id)
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if not _can_read_artifact(auth, artifact):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    
    container = svc._get_container(artifact.artifact_type)
    blob_name = f"{artifact.job_id or 'standalone'}/{artifact.filename}"
    try:
        sas_url = await svc.generate_sas_url(container, blob_name)
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact download URL could not be generated.",
        )
    
    return {"download_url": sas_url}
