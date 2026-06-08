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
    get_settings.cache_clear()

    result = await DocumentProcessingService().extract(
        "scan.png",
        "image/png",
        b"not a real image",
    )

    assert result.status == "needs_ocr"
    assert result.source == "azure_document_intelligence"
    assert "endpoint is not configured" in (result.error or "")
