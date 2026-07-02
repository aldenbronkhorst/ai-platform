import hashlib
import asyncio
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from app.models.models import AIArtifact
from app.schemas.schemas import AIArtifactCreate
from app.core.config import get_settings
from app.services.document_processing import (
    DocumentExtractionResult,
    DocumentProcessingService,
    OCR_PROFILE_LAYOUT,
    OCR_PROFILE_TEXT,
    is_supported_document,
)


class ArtifactService:
    CHAT_UPLOAD_CONTAINER = "job-files"

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
            if self.settings.azure_storage_connection_string:
                self._blob_client = BlobServiceClient.from_connection_string(
                    self.settings.azure_storage_connection_string
                )
                return self._blob_client

            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            credential = self.settings.azure_storage_account_key or self._get_credential()
            self._blob_client = BlobServiceClient(account_url=account_url, credential=credential)
        return self._blob_client

    def _blob_name(self, artifact_id: UUID, filename: str) -> str:
        return f"{artifact_id}/{filename}"

    def _blob_location(self, artifact: AIArtifact) -> tuple[str, str]:
        parsed = urlparse(artifact.storage_uri or "")
        path = parsed.path.lstrip("/")
        container, _, blob_name = path.partition("/")
        if container and blob_name:
            return container, blob_name
        return self.CHAT_UPLOAD_CONTAINER, self._blob_name(artifact.id, artifact.filename)

    def _apply_extraction_result(self, artifact: AIArtifact, result: DocumentExtractionResult) -> None:
        artifact.extraction_status = result.status
        artifact.extraction_source = result.source
        artifact.extracted_text = result.text
        artifact.extraction_metadata_json = result.metadata or None
        artifact.extraction_error = result.error

    def _has_layout_metadata(self, artifact: AIArtifact) -> bool:
        metadata = getattr(artifact, "extraction_metadata_json", None)
        if not isinstance(metadata, dict):
            return False
        layout = metadata.get("layout")
        if not isinstance(layout, dict):
            return False
        if layout.get("tables"):
            return True
        return bool(layout.get("pages"))

    async def _extract_and_store_text(
        self,
        artifact: AIArtifact,
        file_content: bytes,
        *,
        ocr_profile: str = OCR_PROFILE_TEXT,
    ) -> None:
        try:
            result = await DocumentProcessingService().extract(
                artifact.filename,
                artifact.mime_type,
                file_content,
                ocr_profile=ocr_profile,
            )
        except Exception as exc:
            result = DocumentExtractionResult(
                status="failed",
                source="document_reader",
                error=str(exc),
            )
        self._apply_extraction_result(artifact, result)
        await self.db.flush()

    async def upload(
        self,
        data: AIArtifactCreate,
        file_content: bytes,
        created_by_user_id: Optional[UUID] = None,
        *,
        artifact_type: str = "chat-upload",
    ) -> AIArtifact:
        artifact_id = uuid4()
        container = self.CHAT_UPLOAD_CONTAINER
        blob_name = self._blob_name(artifact_id, data.filename)

        blob_client = self._get_blob_client().get_blob_client(container=container, blob=blob_name)
        await asyncio.to_thread(blob_client.upload_blob, file_content, overwrite=True)

        sha256 = hashlib.sha256(file_content).hexdigest()
        storage_uri = f"https://{self.settings.storage_account_name}.blob.core.windows.net/{container}/{blob_name}"

        artifact = AIArtifact(
            id=artifact_id,
            artifact_type=artifact_type,
            filename=data.filename,
            mime_type=data.mime_type,
            storage_uri=storage_uri,
            sha256=sha256,
            created_by_user_id=created_by_user_id,
            extraction_status="pending" if is_supported_document(data.filename, data.mime_type) else "not_required",
        )
        self.db.add(artifact)
        await self.db.flush()
        return artifact

    async def create_from_bytes(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        artifact_type: str,
        created_by_user_id: Optional[UUID] = None,
    ) -> AIArtifact:
        return await self.upload(
            AIArtifactCreate(filename=filename, mime_type=mime_type),
            content,
            created_by_user_id=created_by_user_id,
            artifact_type=artifact_type,
        )

    async def download_content(self, artifact: AIArtifact) -> bytes:
        container, blob_name = self._blob_location(artifact)
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
        text = await self.readable_text(artifact)
        if text is None:
            if getattr(artifact, "extraction_status", None) in {"needs_ocr", "failed"}:
                error = getattr(artifact, "extraction_error", None)
                detail = f" {error}" if error else ""
                return f"[Document Reader could not extract text from this file. Status: {artifact.extraction_status}.{detail}]"
            return None
        if not text:
            return None
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}\n[Attachment text truncated to {max_chars} characters.]"

    async def readable_text(self, artifact: AIArtifact, *, require_layout: bool = False) -> Optional[str]:
        """Return the full extracted/readable text for a user artifact.

        This is the hosted equivalent of Hermes' file-read base layer: OCR or
        native extraction happens once, then the text remains available for
        paged reads by the model or Workspace broker.
        """
        if not self.supports_text_preview(artifact):
            return None

        if getattr(artifact, "extracted_text", None) and (
            not require_layout or self._has_layout_metadata(artifact)
        ):
            return (artifact.extracted_text or "").replace("\x00", "").strip()

        if is_supported_document(artifact.filename, artifact.mime_type):
            status = getattr(artifact, "extraction_status", None) or "not_required"
            should_attempt = require_layout or status in {"not_required", "queued", "pending", "processing"} or (
                status == "needs_ocr" and bool(self.settings.azure_document_intelligence_endpoint)
            )
            if should_attempt:
                content = await self.download_content(artifact)
                await self._extract_and_store_text(
                    artifact,
                    content,
                    ocr_profile=OCR_PROFILE_LAYOUT if require_layout else OCR_PROFILE_TEXT,
                )
                if artifact.extracted_text:
                    return artifact.extracted_text.replace("\x00", "").strip()
            return None

        content = await self.download_content(artifact)
        return content.decode("utf-8", errors="replace").replace("\x00", "").strip()

    async def get_by_id(self, artifact_id: UUID) -> Optional[AIArtifact]:
        result = await self.db.execute(select(AIArtifact).where(AIArtifact.id == artifact_id))
        return result.scalar_one_or_none()
