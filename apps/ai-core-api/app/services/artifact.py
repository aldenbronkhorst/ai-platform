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
from app.services.document_processing import (
    DocumentExtractionResult,
    DocumentProcessingService,
    is_supported_document,
)


class ArtifactService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()
        self._blob_client = None
        self._credential = None

    def _get_credential(self):
        if self._credential is None:
            kwargs = {}
            if self.settings.azure_client_id:
                kwargs["managed_identity_client_id"] = self.settings.azure_client_id
            self._credential = DefaultAzureCredential(**kwargs)
        return self._credential

    def _get_blob_client(self) -> BlobServiceClient:
        if self._blob_client is None:
            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            self._blob_client = BlobServiceClient(account_url=account_url, credential=self._get_credential())
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

    def _blob_name(self, job_id: Optional[UUID], filename: str) -> str:
        return f"{job_id or 'standalone'}/{filename}"

    def _apply_extraction_result(self, artifact: AIArtifact, result: DocumentExtractionResult) -> None:
        artifact.extraction_status = result.status
        artifact.extraction_source = result.source
        artifact.extracted_text = result.text
        artifact.extraction_metadata_json = result.metadata or None
        artifact.extraction_error = result.error

    async def _extract_and_store_text(self, artifact: AIArtifact, file_content: bytes) -> None:
        try:
            result = await DocumentProcessingService().extract(
                artifact.filename,
                artifact.mime_type,
                file_content,
            )
        except Exception as exc:
            result = DocumentExtractionResult(
                status="failed",
                source="document_reader",
                error=str(exc),
            )
        self._apply_extraction_result(artifact, result)
        await self.db.flush()

    async def upload(self, data: AIArtifactCreate, file_content: bytes, created_by_user_id: Optional[UUID] = None) -> AIArtifact:
        container = self._get_container(data.artifact_type)
        blob_name = self._blob_name(data.job_id, data.filename)

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
        if is_supported_document(artifact.filename, artifact.mime_type):
            await self._extract_and_store_text(artifact, file_content)
        return artifact

    async def download_content(self, artifact: AIArtifact) -> bytes:
        container = self._get_container(artifact.artifact_type)
        blob_name = self._blob_name(artifact.job_id, artifact.filename)
        blob_client = self._get_blob_client().get_blob_client(container=container, blob=blob_name)
        stream = await asyncio.to_thread(blob_client.download_blob)
        return await asyncio.to_thread(stream.readall)

    def supports_text_preview(self, artifact: AIArtifact) -> bool:
        mime_type = (artifact.mime_type or "").lower()
        filename = (artifact.filename or "").lower()
        if is_supported_document(filename, mime_type):
            return True
        if mime_type.startswith("text/"):
            return True
        if mime_type in {
            "application/json",
            "application/xml",
            "application/csv",
            "application/x-ndjson",
            "application/yaml",
            "application/x-yaml",
        }:
            return True
        return filename.endswith((".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".md", ".yaml", ".yml", ".log"))

    async def text_preview(self, artifact: AIArtifact, max_chars: int = 12_000) -> Optional[str]:
        if not self.supports_text_preview(artifact):
            return None

        if getattr(artifact, "extracted_text", None):
            text = (artifact.extracted_text or "").strip()
            if len(text) <= max_chars:
                return text
            return f"{text[:max_chars].rstrip()}\n[Attachment text truncated to {max_chars} characters.]"

        if is_supported_document(artifact.filename, artifact.mime_type):
            status = getattr(artifact, "extraction_status", None) or "not_required"
            should_attempt = status in {"not_required", "queued", "pending", "processing"} or (
                status == "needs_ocr" and bool(self.settings.azure_document_intelligence_endpoint)
            )
            if should_attempt:
                content = await self.download_content(artifact)
                await self._extract_and_store_text(artifact, content)
                if artifact.extracted_text:
                    text = artifact.extracted_text.strip()
                    if len(text) <= max_chars:
                        return text
                    return f"{text[:max_chars].rstrip()}\n[Attachment text truncated to {max_chars} characters.]"

            if getattr(artifact, "extraction_status", None) in {"needs_ocr", "failed"}:
                error = getattr(artifact, "extraction_error", None)
                detail = f" {error}" if error else ""
                return f"[Document Reader could not extract text from this file. Status: {artifact.extraction_status}.{detail}]"

            return None

        content = await self.download_content(artifact)
        text = content.decode("utf-8", errors="replace").replace("\x00", "").strip()
        if not text:
            return None
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}\n[Attachment text truncated to {max_chars} characters.]"

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
            from azure.storage.blob import BlobServiceClient
            blob_service = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=self._get_credential(),
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
