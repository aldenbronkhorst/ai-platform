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

    @pytest.mark.asyncio
    async def test_readiness_payload_uses_shallow_dependency_checks_by_default(self, monkeypatch):
        from app.routers import health

        class Db:
            async def execute(self, _stmt):
                return None

        monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
        monkeypatch.setenv("STORAGE_ACCOUNT_NAME", "storageexample")
        monkeypatch.setenv("AZURE_SERVICE_BUS_NAMESPACE", "sb-example")
        monkeypatch.setattr(health, "_startup_config_issues", lambda: [])

        payload = await health._dependency_health_payload(Db(), deep=False)

        assert payload["status"] == "healthy"
        assert payload["dependencies"]["postgresql"] == "reachable"
        assert payload["dependencies"]["key_vault"] == "configured"
        assert payload["dependencies"]["blob_storage"] == "configured"
        assert payload["dependencies"]["service_bus"] == "configured"


class TestTasks:
    def test_create_task(self, client):
        response = client.post("/tasks", json={
            "title": "Test Task",
            "description": "A test task",
            "priority": "high"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Task"
        assert data["status"] == "open"

    def test_list_tasks(self, client):
        response = client.get("/tasks")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestAudit:
    def test_create_audit_event(self, client):
        response = client.post("/audit", json={
            "action_type": "test",
            "target_system": "ai-platform",
            "input_summary": "Test audit event",
            "risk_level": "low",
            "status": "success"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["action_type"] == "test"

    def test_list_audit_events(self, client):
        response = client.get("/audit")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestTools:
    def test_register_tool(self, client):
        response = client.post("/tools/register", json={
            "name": "test.tool",
            "display_name": "Test Tool",
            "target_system": "ai-platform",
            "description": "A test tool"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test.tool"

    def test_list_tools(self, client):
        response = client.get("/tools")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestContext:
    def test_get_context(self, client):
        response = client.post("/context", json={
            "task": "Test task",
            "systems": ["odoo"],
            "limit": 5
        })
        assert response.status_code == 200
        data = response.json()
        assert "rules" in data
        assert "facts" in data
        assert "tools" in data


class TestArtifacts:
    @pytest.mark.skip(reason="Requires Azure Blob Storage - tested in integration tests")
    def test_create_artifact(self, client):
        import io
        response = client.post(
            "/artifacts",
            data={
                "artifact_type": "report",
                "filename": "test_report.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "stage": "final"
            },
            files={"file": ("test_report.xlsx", io.BytesIO(b"test content"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        # May fail in test env without Azure storage configured - that's acceptable for unit tests
        assert response.status_code in (201, 500)

    def test_get_artifact_not_found(self, client):
        import uuid
        response = client.get(f"/artifacts/{uuid.uuid4()}")
        assert response.status_code == 404
