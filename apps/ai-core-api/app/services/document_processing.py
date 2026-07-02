"""Document text extraction for uploaded artifacts.

This service intentionally keeps OCR on Azure Document Intelligence. Local
libraries are used only for native text extraction from text-based PDFs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Protocol

from azure.identity import DefaultAzureCredential

from app.core.config import get_settings

logger = logging.getLogger(__name__)


PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
IMAGE_MIME_PREFIX = "image/"
NATIVE_PDF_MIN_CHARS = 1
OCR_PROFILE_TEXT = "text"
OCR_PROFILE_LAYOUT = "layout"
OCR_PROFILES = {OCR_PROFILE_TEXT, OCR_PROFILE_LAYOUT}


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


def _compact_cell_text(value: Any, max_chars: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _polygon_points(value: Any) -> list[dict[str, float]]:
    if not value:
        return []

    points: list[dict[str, float]] = []
    if isinstance(value, (list, tuple)):
        if value and all(hasattr(point, "x") and hasattr(point, "y") for point in value):
            for point in value:
                try:
                    points.append({"x": float(point.x), "y": float(point.y)})
                except (TypeError, ValueError):
                    continue
            return points

        if len(value) % 2 == 0 and all(isinstance(item, (int, float)) for item in value):
            iterator = iter(value)
            for x_value, y_value in zip(iterator, iterator):
                points.append({"x": float(x_value), "y": float(y_value)})
            return points

    return points


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>").strip()


def _unique_headers(values: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    headers: list[str] = []
    for index, value in enumerate(values, start=1):
        base = value.strip() or f"Column {index}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        headers.append(base if count == 1 else f"{base} {count}")
    return headers


def _grid_from_cells(row_count: int, column_count: int, cells: list[dict[str, Any]]) -> list[list[str]]:
    if row_count <= 0 or column_count <= 0:
        return []

    grid = [["" for _ in range(column_count)] for _ in range(row_count)]
    for cell in cells:
        row = _as_int(cell.get("row"), -1)
        column = _as_int(cell.get("column"), -1)
        if row < 0 or column < 0 or row >= row_count or column >= column_count:
            continue
        grid[row][column] = str(cell.get("text") or "")
    return grid


def _table_markdown(row_count: int, column_count: int, cells: list[dict[str, Any]], max_chars: int) -> str:
    grid = _grid_from_cells(row_count, column_count, cells)
    if not grid:
        return ""

    header_rows = sorted({
        _as_int(cell.get("row"), -1)
        for cell in cells
        if "header" in str(cell.get("kind") or "").lower()
    })
    header_index = header_rows[0] if header_rows and 0 <= header_rows[0] < len(grid) else 0
    headers = _unique_headers([_escape_markdown_cell(value) for value in grid[header_index]])

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row_index, row in enumerate(grid):
        if row_index == header_index:
            continue
        lines.append("| " + " | ".join(_escape_markdown_cell(value) for value in row) + " |")
        if sum(len(line) + 1 for line in lines) > max_chars:
            lines.append("| [table truncated] | " + " | ".join("" for _ in headers[1:]) + " |")
            break

    markdown = "\n".join(lines)
    if len(markdown) <= max_chars:
        return markdown
    return markdown[:max_chars].rstrip()


def _normalize_page_lines(result: Any, max_lines: int, max_chars: int) -> tuple[list[dict[str, Any]], bool]:
    pages: list[dict[str, Any]] = []
    remaining = max_lines
    truncated = False

    for page in getattr(result, "pages", []) or []:
        page_lines: list[dict[str, Any]] = []
        for line in getattr(page, "lines", []) or []:
            if remaining <= 0:
                truncated = True
                break
            page_lines.append({
                "text": _compact_cell_text(getattr(line, "content", ""), max_chars),
                "polygon": _polygon_points(getattr(line, "polygon", None)),
            })
            remaining -= 1
        pages.append({
            "page_number": getattr(page, "page_number", None),
            "width": getattr(page, "width", None),
            "height": getattr(page, "height", None),
            "unit": getattr(page, "unit", None),
            "lines": page_lines,
        })
        if truncated:
            break

    return pages, truncated


def _normalize_tables(result: Any, settings: Any) -> tuple[list[dict[str, Any]], bool]:
    tables: list[dict[str, Any]] = []
    max_tables = max(0, settings.document_layout_max_tables)
    max_cells = max(0, settings.document_layout_max_cells)
    remaining_cells = max_cells
    truncated = False

    for table_index, table in enumerate(getattr(result, "tables", []) or [], start=1):
        if len(tables) >= max_tables:
            truncated = True
            break

        row_count = _as_int(getattr(table, "row_count", 0))
        column_count = _as_int(getattr(table, "column_count", 0))
        cells: list[dict[str, Any]] = []
        for cell in getattr(table, "cells", []) or []:
            if remaining_cells <= 0:
                truncated = True
                break
            bounding_regions = []
            for region in getattr(cell, "bounding_regions", []) or []:
                bounding_regions.append({
                    "page_number": getattr(region, "page_number", None),
                    "polygon": _polygon_points(getattr(region, "polygon", None)),
                })
            cells.append({
                "row": _as_int(getattr(cell, "row_index", 0)),
                "column": _as_int(getattr(cell, "column_index", 0)),
                "row_span": _as_int(getattr(cell, "row_span", 1), 1),
                "column_span": _as_int(getattr(cell, "column_span", 1), 1),
                "kind": getattr(cell, "kind", None),
                "text": _compact_cell_text(
                    getattr(cell, "content", ""),
                    settings.document_layout_cell_max_chars,
                ),
                "bounding_regions": bounding_regions,
            })
            remaining_cells -= 1

        grid = _grid_from_cells(row_count, column_count, cells)
        tables.append({
            "table_index": table_index,
            "row_count": row_count,
            "column_count": column_count,
            "cell_count": len(cells),
            "truncated": truncated,
            "rows": [{"row_index": index, "values": row} for index, row in enumerate(grid)],
            "cells": cells,
            "markdown": _table_markdown(
                row_count,
                column_count,
                cells,
                settings.document_layout_markdown_max_chars,
            ),
        })
        if truncated:
            break

    return tables, truncated


def _layout_metadata(result: Any, settings: Any) -> dict[str, Any]:
    pages, lines_truncated = _normalize_page_lines(
        result,
        max(0, settings.document_layout_max_lines),
        settings.document_layout_cell_max_chars,
    )
    tables, tables_truncated = _normalize_tables(result, settings)
    return {
        "page_count": len(getattr(result, "pages", []) or []),
        "pages": pages,
        "lines_truncated": lines_truncated,
        "table_count": len(getattr(result, "tables", []) or []),
        "tables": tables,
        "tables_truncated": tables_truncated,
    }


def is_supported_document(filename: str, mime_type: str) -> bool:
    normalized_mime = (mime_type or "").lower()
    normalized_name = (filename or "").lower()
    return (
        normalized_mime in PDF_MIME_TYPES
        or normalized_name.endswith(".pdf")
        or normalized_mime.startswith(IMAGE_MIME_PREFIX)
    )


class OcrProvider(Protocol):
    name: str

    def extract(self, content: bytes, mime_type: str, *, profile: str = OCR_PROFILE_TEXT) -> DocumentExtractionResult:
        ...


class DisabledOcrProvider:
    name = "disabled"

    def extract(self, _content: bytes, _mime_type: str, *, profile: str = OCR_PROFILE_TEXT) -> DocumentExtractionResult:
        return DocumentExtractionResult(
            status="needs_ocr",
            source=self.name,
            error="Document OCR provider is disabled.",
        )


class AzureDocumentIntelligenceOcrProvider:
    name = "azure_document_intelligence"

    def __init__(self, settings: Any):
        self.settings = settings

    def _credential(self):
        if self.settings.azure_document_intelligence_key:
            from azure.core.credentials import AzureKeyCredential

            return AzureKeyCredential(self.settings.azure_document_intelligence_key)

        kwargs = {}
        if self.settings.azure_client_id:
            kwargs["managed_identity_client_id"] = self.settings.azure_client_id
        return DefaultAzureCredential(**kwargs)

    def _model_id_for_profile(self, profile: str) -> str:
        if profile == OCR_PROFILE_LAYOUT:
            return self.settings.document_ocr_layout_model_id or "prebuilt-layout"
        return self.settings.document_ocr_read_model_id or "prebuilt-read"

    def extract(self, content: bytes, mime_type: str, *, profile: str = OCR_PROFILE_TEXT) -> DocumentExtractionResult:
        if not self.settings.azure_document_intelligence_endpoint:
            return DocumentExtractionResult(
                status="needs_ocr",
                source=self.name,
                error="Azure Document Intelligence endpoint is not configured.",
            )

        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
        except Exception as exc:
            return DocumentExtractionResult(
                status="failed",
                source=self.name,
                error=f"Azure Document Intelligence SDK is not available: {exc}",
            )

        model_id = self._model_id_for_profile(profile)
        try:
            client = DocumentIntelligenceClient(
                endpoint=self.settings.azure_document_intelligence_endpoint,
                credential=self._credential(),
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
            layout = _layout_metadata(result, self.settings)
            return DocumentExtractionResult(
                status="ready" if clean else "failed",
                text=clean or None,
                source=f"{self.name}:{model_id}",
                metadata={
                    "provider": self.name,
                    "model_id": model_id,
                    "ocr_profile": profile,
                    "character_count": len(clean),
                    "truncated": truncated,
                    "page_count": layout["page_count"],
                    "layout": layout,
                },
                error=None if clean else "Azure Document Intelligence returned no text.",
            )
        except Exception as exc:
            logger.warning("Azure Document Intelligence extraction failed: %s", exc)
            return DocumentExtractionResult(
                status="failed",
                source=f"{self.name}:{model_id}",
                error=str(exc),
            )


def build_ocr_provider(settings: Any) -> OcrProvider:
    provider = (settings.document_ocr_provider or "azure_document_intelligence").strip().lower()
    if provider in {"", "none", "disabled", "off"}:
        return DisabledOcrProvider()
    if provider in {"azure", "azure_document_intelligence", "document_intelligence"}:
        return AzureDocumentIntelligenceOcrProvider(settings)
    return DisabledOcrProvider()


class DocumentProcessingService:
    def __init__(self, ocr_provider: OcrProvider | None = None):
        self.settings = get_settings()
        self.ocr_provider = ocr_provider or build_ocr_provider(self.settings)

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

    async def extract(
        self,
        filename: str,
        mime_type: str,
        content: bytes,
        *,
        ocr_profile: str = OCR_PROFILE_TEXT,
    ) -> DocumentExtractionResult:
        if not is_supported_document(filename, mime_type):
            return DocumentExtractionResult(status="not_required")

        if ocr_profile not in OCR_PROFILES:
            return DocumentExtractionResult(
                status="failed",
                source="document_reader",
                error=f"Unknown OCR profile: {ocr_profile}",
            )

        normalized_mime = (mime_type or "").lower()
        normalized_name = (filename or "").lower()
        is_pdf = normalized_mime in PDF_MIME_TYPES or normalized_name.endswith(".pdf")

        if is_pdf and ocr_profile == OCR_PROFILE_TEXT:
            native = await asyncio.to_thread(self._extract_pdf_native, content)
            if native.status == "ready":
                return native

        ocr_result = await asyncio.to_thread(
            self.ocr_provider.extract,
            content,
            mime_type,
            profile=ocr_profile,
        )
        if ocr_result.status == "ready" or not is_pdf or ocr_profile != OCR_PROFILE_LAYOUT:
            return ocr_result

        native = await asyncio.to_thread(self._extract_pdf_native, content)
        if native.status == "ready":
            native.metadata["layout_attempt_error"] = ocr_result.error
            return native
        return ocr_result
