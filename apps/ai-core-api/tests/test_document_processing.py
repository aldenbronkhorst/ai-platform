import sys
import types

import pytest


def _sample_pdf_bytes(text: str = "Employment Agreement PDF extraction smoke test") -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return doc.tobytes()


@pytest.mark.asyncio
async def test_native_pdf_text_extraction_returns_ready(monkeypatch):
    from app.core.config import get_settings
    from app.services.document_processing import DocumentProcessingService

    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    get_settings.cache_clear()

    result = await DocumentProcessingService().extract(
        "agreement.pdf",
        "application/pdf",
        _sample_pdf_bytes("Employment Agreement Native PDF Text"),
    )

    assert result.status == "ready"
    assert result.source == "native_pdf"
    assert "Employment Agreement Native PDF Text" in (result.text or "")
    assert result.metadata["page_count"] == 1


@pytest.mark.asyncio
async def test_ocr_fallback_requires_azure_document_intelligence(monkeypatch):
    from app.core.config import get_settings
    from app.services.document_processing import DocumentProcessingService

    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", raising=False)
    monkeypatch.delenv("DOCUMENT_OCR_PROVIDER", raising=False)
    get_settings.cache_clear()

    result = await DocumentProcessingService().extract(
        "scan.png",
        "image/png",
        b"not a real image",
    )

    assert result.status == "needs_ocr"
    assert result.source == "azure_document_intelligence"
    assert "endpoint is not configured" in (result.error or "")


@pytest.mark.asyncio
async def test_document_processing_accepts_swappable_ocr_provider():
    from app.services.document_processing import DocumentExtractionResult, DocumentProcessingService

    class FakeOcrProvider:
        name = "fake_ocr"

        def extract(
            self,
            content: bytes,
            mime_type: str,
            *,
            profile: str = "text",
        ) -> DocumentExtractionResult:
            return DocumentExtractionResult(
                status="ready",
                text=f"{profile}:{mime_type}:{content.decode('utf-8')}",
                source=self.name,
                metadata={"provider": self.name},
            )

    result = await DocumentProcessingService(ocr_provider=FakeOcrProvider()).extract(
        "scan.png",
        "image/png",
        b"image text",
    )

    assert result.status == "ready"
    assert result.source == "fake_ocr"
    assert result.text == "text:image/png:image text"
    assert result.metadata["provider"] == "fake_ocr"


@pytest.mark.asyncio
async def test_azure_layout_extraction_preserves_structured_tables(monkeypatch):
    from app.core.config import get_settings
    from app.services.document_processing import DocumentProcessingService

    class FakePoller:
        def result(self):
            cell = lambda row, column, content, kind=None: types.SimpleNamespace(
                row_index=row,
                column_index=column,
                content=content,
                kind=kind,
                row_span=1,
                column_span=1,
                bounding_regions=[],
            )
            return types.SimpleNamespace(
                content=(
                    "STK-CODE DESCRIPTION PRICE\n"
                    "020283 Subaru Lip Pencil 5.650\n"
                    "020908 Pawpaw Cream 29.150"
                ),
                pages=[
                    types.SimpleNamespace(
                        page_number=1,
                        width=8.5,
                        height=11,
                        unit="inch",
                        lines=[
                            types.SimpleNamespace(
                                content="020283 Subaru Lip Pencil 5.650",
                                polygon=[0, 0, 1, 0, 1, 1, 0, 1],
                            )
                        ],
                    )
                ],
                tables=[
                    types.SimpleNamespace(
                        row_count=3,
                        column_count=3,
                        cells=[
                            cell(0, 0, "STK-CODE", "columnHeader"),
                            cell(0, 1, "DESCRIPTION", "columnHeader"),
                            cell(0, 2, "PRICE", "columnHeader"),
                            cell(1, 0, "020283"),
                            cell(1, 1, "Subaru Lip Pencil"),
                            cell(1, 2, "5.650"),
                            cell(2, 0, "020908"),
                            cell(2, 1, "Pawpaw Cream"),
                            cell(2, 2, "29.150"),
                        ],
                    )
                ],
            )

    requested = {}

    class FakeDocumentIntelligenceClient:
        def __init__(self, endpoint, credential):
            requested["endpoint"] = endpoint
            requested["credential"] = credential

        def begin_analyze_document(self, model_id, body, content_type):
            requested["model_id"] = model_id
            requested["content_type"] = content_type
            return FakePoller()

        def close(self):
            requested["closed"] = True

    fake_module = types.ModuleType("azure.ai.documentintelligence")
    fake_module.DocumentIntelligenceClient = FakeDocumentIntelligenceClient
    monkeypatch.setitem(sys.modules, "azure.ai", types.ModuleType("azure.ai"))
    monkeypatch.setitem(sys.modules, "azure.ai.documentintelligence", fake_module)
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://docs.example")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "test-key")
    monkeypatch.delenv("DOCUMENT_OCR_READ_MODEL_ID", raising=False)
    monkeypatch.delenv("DOCUMENT_OCR_LAYOUT_MODEL_ID", raising=False)
    get_settings.cache_clear()

    result = await DocumentProcessingService().extract(
        "scan.png",
        "image/png",
        b"not a real image",
        ocr_profile="layout",
    )

    assert result.status == "ready"
    assert result.source == "azure_document_intelligence:prebuilt-layout"
    assert requested["model_id"] == "prebuilt-layout"
    assert requested["closed"] is True
    table = result.metadata["layout"]["tables"][0]
    assert table["rows"][1]["values"] == ["020283", "Subaru Lip Pencil", "5.650"]
    assert table["rows"][2]["values"] == ["020908", "Pawpaw Cream", "29.150"]
    assert "| 020283 | Subaru Lip Pencil | 5.650 |" in table["markdown"]
    get_settings.cache_clear()
