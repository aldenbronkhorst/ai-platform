import io

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_create_artifact_upload_failure_returns_structured_503(monkeypatch):
    async def fail_upload(self, data, file_content, created_by_user_id=None):
        raise RuntimeError("storage denied")

    monkeypatch.setattr("app.routers.artifact.ArtifactService.upload", fail_upload)

    response = client.post(
        "/artifacts",
        files={"file": ("statement.csv", io.BytesIO(b"col\n1\n"), "text/csv")},
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_type"] == "artifact_upload_failed"
    assert "artifact storage" in detail["error_message"]


def test_artifact_service_passes_user_assigned_identity_client_id(monkeypatch):
    from app.services import artifact as artifact_service
    from app.services.artifact import ArtifactService

    captured_kwargs = {}

    class FakeCredential:
        pass

    def fake_default_credential(**kwargs):
        captured_kwargs.update(kwargs)
        return FakeCredential()

    monkeypatch.setattr(artifact_service, "DefaultAzureCredential", fake_default_credential)

    service = ArtifactService(db=None)
    service.settings.azure_client_id = "client-id-123"

    assert isinstance(service._get_credential(), FakeCredential)
    assert captured_kwargs == {"managed_identity_client_id": "client-id-123"}


def test_artifact_service_prefers_storage_connection_string(monkeypatch):
    from app.services import artifact as artifact_service
    from app.services.artifact import ArtifactService

    captured = {}

    class FakeBlobServiceClient:
        @classmethod
        def from_connection_string(cls, value):
            captured["connection_string"] = value
            return cls()

    monkeypatch.setattr(artifact_service, "BlobServiceClient", FakeBlobServiceClient)

    service = ArtifactService(db=None)
    service.settings.azure_storage_connection_string = "UseDevelopmentStorage=true"

    assert isinstance(service._get_blob_client(), FakeBlobServiceClient)
    assert captured == {"connection_string": "UseDevelopmentStorage=true"}


def test_artifact_service_uses_storage_account_key_before_default_credential(monkeypatch):
    from app.services import artifact as artifact_service
    from app.services.artifact import ArtifactService

    captured = {}

    class FakeBlobServiceClient:
        def __init__(self, account_url, credential):
            captured["account_url"] = account_url
            captured["credential"] = credential

    def fail_default_credential(*_args, **_kwargs):
        raise AssertionError("DefaultAzureCredential should not be used when a storage key is configured")

    monkeypatch.setattr(artifact_service, "BlobServiceClient", FakeBlobServiceClient)
    monkeypatch.setattr(artifact_service, "DefaultAzureCredential", fail_default_credential)

    service = ArtifactService(db=None)
    service.settings.storage_account_name = "storageexample"
    service.settings.azure_storage_account_key = "account-key"

    assert isinstance(service._get_blob_client(), FakeBlobServiceClient)
    assert captured == {
        "account_url": "https://storageexample.blob.core.windows.net",
        "credential": "account-key",
    }


def test_artifact_service_supports_pdf_text_preview():
    from app.models.models import AIArtifact
    from app.services.artifact import ArtifactService

    artifact = AIArtifact(
        artifact_type="job-file",
        filename="Employment Agreement.pdf",
        mime_type="application/pdf",
        storage_uri="https://storage.example/job-files/standalone/Employment Agreement.pdf",
    )

    assert ArtifactService(db=None).supports_text_preview(artifact)


@pytest.mark.asyncio
async def test_artifact_upload_marks_pdf_pending_without_running_ocr(monkeypatch):
    from app.schemas.schemas import AIArtifactCreate
    from app.services.artifact import ArtifactService

    class FakeBlobClient:
        def upload_blob(self, _content, overwrite=False):
            assert overwrite is True

    class FakeBlobServiceClient:
        def get_blob_client(self, container, blob):
            assert container == ArtifactService.CHAT_UPLOAD_CONTAINER
            assert blob.endswith("/invoice.pdf")
            return FakeBlobClient()

    class FakeDb:
        def __init__(self):
            self.added = None

        def add(self, artifact):
            self.added = artifact

        async def flush(self):
            pass

    async def fail_extract(self, artifact, file_content):
        raise AssertionError("upload should not run OCR synchronously")

    db = FakeDb()
    service = ArtifactService(db=db)
    service.settings.storage_account_name = "storageexample"
    monkeypatch.setattr(service, "_get_blob_client", lambda: FakeBlobServiceClient())
    monkeypatch.setattr(ArtifactService, "_extract_and_store_text", fail_extract)

    artifact = await service.upload(
        AIArtifactCreate(filename="invoice.pdf", mime_type="application/pdf"),
        b"%PDF",
    )

    assert db.added is artifact
    assert artifact.extraction_status == "pending"
    assert artifact.extracted_text is None
