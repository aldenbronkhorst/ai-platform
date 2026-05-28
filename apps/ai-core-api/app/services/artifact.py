import hashlib
import io
from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from app.models.models import AIArtifact
from app.schemas.schemas import AIArtifactCreate
from app.core.config import get_settings


class ArtifactService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()
        self._blob_client = None

    def _get_blob_client(self) -> BlobServiceClient:
        if self._blob_client is None:
            credential = DefaultAzureCredential()
            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            self._blob_client = BlobServiceClient(account_url=account_url, credential=credential)
        return self._blob_client

    def _get_container(self, artifact_type: str) -> str:
        mapping = {
            "ocr": "ocr",
            "report": "reports",
            "raw-export": "raw-exports",
            "runner-log": "runner-logs",
            "job-file": "job-files",
            "evidence": "evidence",
            "debug": "temp",
            "intermediate": "temp",
            "final": "artifacts",
        }
        return mapping.get(artifact_type, "artifacts")

    async def upload(self, data: AIArtifactCreate, file_content: bytes, created_by_user_id: Optional[UUID] = None) -> AIArtifact:
        container = self._get_container(data.artifact_type)
        blob_name = f"{data.job_id or 'standalone'}/{data.filename}"

        # Upload to Blob Storage
        blob_client = self._get_blob_client().get_blob_client(container=container, blob=blob_name)
        blob_client.upload_blob(file_content, overwrite=True)

        sha256 = hashlib.sha256(file_content).hexdigest()
        storage_uri = f"https://{self.settings.storage_account_name}.blob.core.windows.net/{container}/{blob_name}"

        artifact = AIArtifact(
            **data.model_dump(exclude_unset=True),
            storage_uri=storage_uri,
            sha256=sha256,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(artifact)
        await self.db.flush()
        return artifact

    async def get_by_id(self, artifact_id: UUID) -> Optional[AIArtifact]:
        result = await self.db.execute(select(AIArtifact).where(AIArtifact.id == artifact_id))
        return result.scalar_one_or_none()
