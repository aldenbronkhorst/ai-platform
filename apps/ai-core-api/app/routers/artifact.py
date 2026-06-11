import logging

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
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
