import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIArtifact
from app.services.artifact import ArtifactService
from app.schemas.schemas import AIArtifactCreate, AIArtifactResponse

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
logger = logging.getLogger(__name__)


@router.post("", response_model=AIArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    svc = ArtifactService(db)
    data = AIArtifactCreate(
        filename=file.filename or "upload",
        mime_type=file.content_type or "application/octet-stream",
    )
    content = await file.read()
    try:
        artifact = await svc.upload(data, content, created_by_user_id=auth.get("user_id"))
    except Exception as exc:
        logger.exception("Artifact upload failed | filename=%s", data.filename)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "artifact_upload_failed",
                "error_message": "The file could not be uploaded to artifact storage.",
            },
        ) from exc

    return artifact


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_key_auth),
):
    try:
        from uuid import UUID

        artifact_uuid = UUID(artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found") from exc

    result = await db.execute(
        select(AIArtifact).where(
            AIArtifact.id == artifact_uuid,
            AIArtifact.created_by_user_id == auth.get("user_id"),
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    svc = ArtifactService(db)
    content = await svc.download_content(artifact)
    filename = artifact.filename or "download"
    encoded = quote(filename)
    disposition = f"attachment; filename*=UTF-8''{encoded}"
    if (artifact.mime_type or "").startswith(("image/", "text/")) or artifact.mime_type == "application/pdf":
        disposition = f"inline; filename*=UTF-8''{encoded}"

    return Response(
        content,
        media_type=artifact.mime_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )
