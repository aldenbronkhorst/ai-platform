import hashlib
import asyncio
from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
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

        blob_client = self._get_blob_client().get_blob_client(container=container, blob=blob_name)
        await asyncio.to_thread(blob_client.upload_blob, file_content, overwrite=True)

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

    async def upload_json(self, data: AIArtifactCreate, json_content: dict, created_by_user_id: Optional[UUID] = None) -> AIArtifact:
        import json
        file_content = json.dumps(json_content, default=str).encode("utf-8")
        if not data.filename.endswith(".json"):
            data.filename = data.filename + ".json"
        if not data.mime_type:
            data.mime_type = "application/json"
        return await self.upload(data, file_content, created_by_user_id=created_by_user_id)

    async def get_by_id(self, artifact_id: UUID) -> Optional[AIArtifact]:
        result = await self.db.execute(select(AIArtifact).where(AIArtifact.id == artifact_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: Optional[UUID], limit: int = 50, offset: int = 0) -> list[AIArtifact]:
        stmt = select(AIArtifact).order_by(desc(AIArtifact.created_at)).limit(limit).offset(offset)
        if user_id:
            stmt = stmt.where(AIArtifact.created_by_user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def generate_sas_url(self, container: str, blob_name: str) -> str:
        """Generate a short-lived read-only URL for the blob."""
        from datetime import datetime, timedelta
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions

        account_name = self.settings.storage_account_name
        blob_url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}"

        try:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
            from azure.storage.blob import BlobServiceClient
            blob_service = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=credential,
            )
            user_delegation_key = await asyncio.to_thread(
                blob_service.get_user_delegation_key,
                key_start_time=datetime.utcnow(),
                key_expiry_time=datetime.utcnow() + timedelta(minutes=15),
            )
            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container,
                blob_name=blob_name,
                user_delegation_key=user_delegation_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(minutes=15),
            )
            return f"{blob_url}?{sas_token}"
        except Exception as exc:
            raise RuntimeError("Could not generate a signed artifact download URL.") from exc
