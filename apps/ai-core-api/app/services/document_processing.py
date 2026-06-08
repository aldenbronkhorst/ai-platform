"""Document text extraction for uploaded artifacts.

This service intentionally keeps OCR on Azure Document Intelligence. Local
libraries are used only for native text extraction from text-based PDFs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from azure.identity import DefaultAzureCredential

from app.core.config import get_settings

logger = logging.getLogger(__name__)


PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
IMAGE_MIME_PREFIX = "image/"
NATIVE_PDF_MIN_CHARS = 1


@dataclass
class DocumentExtractionResult:
    status: str
    text: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _clean_text(text: str, max_chars: int) -> tuple[str, bool]:
    clean = text.replace("\x00", "").strip()
    if len(clean) <= max_chars:
        return clean, False
    return clean[:max_chars].rstrip(), True


def is_supported_document(filename: str, mime_type: str) -> bool:
    normalized_mime = (mime_type or "").lower()
    normalized_name = (filename or "").lower()
    return (
        normalized_mime in PDF_MIME_TYPES
        or normalized_name.endswith(".pdf")
        or normalized_mime.startswith(IMAGE_MIME_PREFIX)
    )


class DocumentProcessingService:
    def __init__(self):
        self.settings = get_settings()

    def _azure_credential(self):
        if self.settings.azure_document_intelligence_key:
            from azure.core.credentials import AzureKeyCredential

            return AzureKeyCredential(self.settings.azure_document_intelligence_key)

        kwargs = {}
        if self.settings.azure_client_id:
            kwargs["managed_identity_client_id"] = self.settings.azure_client_id
        return DefaultAzureCredential(**kwargs)

    def _extract_pdf_native(self, content: bytes) -> DocumentExtractionResult:
        try:
            import fitz
        except Exception as exc:
            return DocumentExtractionResult(
                status="failed",
                source="native_pdf",
                error=f"PyMuPDF is not available: {exc}",
            )

        try:
            with fitz.open(stream=content, filetype="pdf") as doc:
                page_text: list[str] = []
                for index, page in enumerate(doc, start=1):
                    text = page.get_text("text").strip()
                    if text:
                        page_text.append(f"[Page {index}]\n{text}")

                clean, truncated = _clean_text(
                    "\n\n".join(page_text),
                    self.settings.document_extraction_max_chars,
                )
                return DocumentExtractionResult(
                    status="ready" if len(clean) >= NATIVE_PDF_MIN_CHARS else "needs_ocr",
                    text=clean or None,
                    source="native_pdf",
                    metadata={
                        "page_count": doc.page_count,
                        "character_count": len(clean),
                        "truncated": truncated,
                    },
                )
        except Exception as exc:
            logger.warning("Native PDF extraction failed: %s", exc)
            return DocumentExtractionResult(status="needs_ocr", source="native_pdf", error=str(exc))

    def _extract_with_azure_document_intelligence(
        self,
        content: bytes,
        mime_type: str,
        model_id: str,
    ) -> DocumentExtractionResult:
        if not self.settings.azure_document_intelligence_endpoint:
            return DocumentExtractionResult(
                status="needs_ocr",
                source="azure_document_intelligence",
                error="Azure Document Intelligence endpoint is not configured.",
            )

        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
        except Exception as exc:
            return DocumentExtractionResult(
                status="failed",
                source="azure_document_intelligence",
                error=f"Azure Document Intelligence SDK is not available: {exc}",
            )

        try:
            client = DocumentIntelligenceClient(
                endpoint=self.settings.azure_document_intelligence_endpoint,
                credential=self._azure_credential(),
            )
            try:
                poller = client.begin_analyze_document(
                    model_id=model_id,
                    body=BytesIO(content),
                    content_type=mime_type or "application/octet-stream",
                )
                result = poller.result()
            finally:
                client.close()
            text = getattr(result, "content", "") or ""
            clean, truncated = _clean_text(text, self.settings.document_extraction_max_chars)
            return DocumentExtractionResult(
                status="ready" if clean else "failed",
                text=clean or None,
                source=f"azure_document_intelligence:{model_id}",
                metadata={
                    "model_id": model_id,
                    "character_count": len(clean),
                    "truncated": truncated,
                    "page_count": len(getattr(result, "pages", []) or []),
                },
                error=None if clean else "Azure Document Intelligence returned no text.",
            )
        except Exception as exc:
            logger.warning("Azure Document Intelligence extraction failed: %s", exc)
            return DocumentExtractionResult(
                status="failed",
                source=f"azure_document_intelligence:{model_id}",
                error=str(exc),
            )

    async def extract(self, filename: str, mime_type: str, content: bytes) -> DocumentExtractionResult:
        if not is_supported_document(filename, mime_type):
            return DocumentExtractionResult(status="not_required")

        normalized_mime = (mime_type or "").lower()
        normalized_name = (filename or "").lower()
        is_pdf = normalized_mime in PDF_MIME_TYPES or normalized_name.endswith(".pdf")

        if is_pdf:
            native = await asyncio.to_thread(self._extract_pdf_native, content)
            if native.status == "ready":
                return native

        return await asyncio.to_thread(
            self._extract_with_azure_document_intelligence,
            content,
            mime_type,
            "prebuilt-read",
        )
