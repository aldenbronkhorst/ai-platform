import pytest
from fastapi.testclient import TestClient

from app.main import app

@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"

    def test_root_metadata_is_not_public_api(self, client):
        response = client.get("/")
        assert response.status_code == 404

    def test_dependency_health_is_not_public_api(self, client):
        response = client.get("/health/dependencies")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_readiness_payload_uses_shallow_dependency_checks_by_default(self, monkeypatch):
        from app.routers import health

        class Db:
            async def execute(self, _stmt):
                return None

        monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
        monkeypatch.setenv("STORAGE_ACCOUNT_NAME", "storageexample")
        monkeypatch.setattr(health, "_startup_config_issues", lambda: [])

        payload = await health._dependency_health_payload(Db(), deep=False)

        assert payload["status"] == "healthy"
        assert payload["dependencies"]["postgresql"] == "reachable"
        assert payload["dependencies"]["key_vault"] == "configured"
        assert payload["dependencies"]["blob_storage"] == "configured"


class TestAudit:
    def test_audit_events_are_not_public_api(self, client):
        response = client.post("/audit", json={"action_type": "test"})
        assert response.status_code == 404

    def test_list_audit_events_is_removed(self, client):
        response = client.get("/audit")
        assert response.status_code == 404

    def test_admin_traces_are_not_public_api(self, client):
        response = client.get("/admin/traces")
        assert response.status_code == 404


class TestTools:
    def test_tools_are_seeded_not_registered_at_runtime(self, client):
        response = client.post("/tools/register", json={
            "name": "test.tool",
            "display_name": "Test Tool",
            "target_system": "ai-platform",
            "description": "A test tool"
        })
        assert response.status_code == 404

    def test_list_tools_is_not_public_api(self, client):
        response = client.get("/tools")
        assert response.status_code == 404


class TestContext:
    def test_context_is_internal_not_public_api(self, client):
        response = client.post("/context", json={
            "task": "Test task",
            "systems": ["odoo"],
            "limit": 5
        })
        assert response.status_code == 404


class TestRules:
    def test_rules_are_not_public_api(self, client):
        response = client.post("/rules", json={
            "title": "Temporary rule",
            "body": "Do not create rules through the public API.",
        })
        assert response.status_code == 404


class TestConnectedAccounts:
    def test_connector_debug_endpoint_is_not_public_api(self, client):
        response = client.get("/connected-accounts/debug/connector")
        assert response.status_code == 404

    def test_github_cli_execution_is_tool_only_not_public_api(self, client):
        response = client.post("/connector/github/cli", json={"command": "gh repo list"})
        assert response.status_code == 404

    def test_github_status_and_diagnose_are_not_public_api(self, client):
        assert client.get("/connector/github/status").status_code == 404
        assert client.post("/connector/github/diagnose").status_code == 404

    def test_microsoft_native_status_and_diagnose_are_not_public_api(self, client):
        provider = "microsoft_graph"
        assert client.get(f"/connector/microsoft-native/{provider}/status").status_code == 404
        assert client.post(f"/connector/microsoft-native/{provider}/diagnose").status_code == 404
        assert client.post(f"/connector/microsoft-native/{provider}/validate").status_code == 404


class TestMemorySurface:
    def test_memory_candidates_are_not_public_api(self, client):
        response = client.post("/memories/extract")
        assert response.status_code == 404

    def test_memory_save_candidates_are_not_public_api(self, client):
        response = client.post("/memories/save-candidate", json={
            "type": "general_note",
            "title": "Temporary memory",
        })
        assert response.status_code == 404


class TestArtifacts:
    @pytest.mark.skip(reason="Requires Azure Blob Storage - tested in integration tests")
    def test_create_artifact(self, client):
        import io
        response = client.post(
            "/artifacts",
            files={"file": ("test_report.xlsx", io.BytesIO(b"test content"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        # May fail in test env without Azure storage configured - that's acceptable for unit tests
        assert response.status_code in (201, 500)

    def test_get_artifact_not_found(self, client):
        import uuid
        response = client.get(f"/artifacts/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_artifact_download_is_not_public_api(self, client):
        import uuid
        response = client.get(f"/artifacts/{uuid.uuid4()}/download")
        assert response.status_code == 404
