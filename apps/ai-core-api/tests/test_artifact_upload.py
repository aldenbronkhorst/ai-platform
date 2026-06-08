import io

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_create_artifact_upload_failure_returns_structured_503(monkeypatch):
    async def fail_upload(self, data, file_content, created_by_user_id=None):
        raise RuntimeError("storage denied")

    monkeypatch.setattr("app.routers.artifact.ArtifactService.upload", fail_upload)

    response = client.post(
        "/artifacts",
        data={
            "artifact_type": "job-file",
            "filename": "statement.csv",
            "mime_type": "text/csv",
        },
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
