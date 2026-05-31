import os
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["EXECUTE_KW_ALLOW_WRITE"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.core.config import get_settings
get_settings.cache_clear()

from app.main import app

client = TestClient(app)

AUTH_HEADERS = {"X-Internal-API-Key": "test-internal-key"}


class MockServerProxy:
    """Mock xmlrpc.client.ServerProxy for Odoo"""

    def __init__(self, url, **kwargs):
        self.url = url
        if "common" in url:
            self.authenticate = MagicMock(return_value=42)
            self.version = MagicMock(return_value={"server_version": "17.0"})
        elif "object" in url:
            self.execute_kw = MagicMock(side_effect=self._execute_kw)

    def _execute_kw(self, db, uid, password, model, method, args, kwargs):
        if method == "search_read":
            return [
                {"id": 1, "name": "Partner One", "email": "partner1@example.com", "is_company": False},
                {"id": 2, "name": "Partner Two", "email": "partner2@example.com", "is_company": True},
            ]
        elif method == "search_count":
            return 2
        elif method == "read":
            return [{"id": 1, "name": "Partner One", "email": "partner1@example.com"}]
        elif method == "fields_get":
            return {
                "name": {"type": "char", "string": "Name", "required": True},
                "email": {"type": "char", "string": "Email"},
                "is_company": {"type": "boolean", "string": "Is a Company"},
            }
        elif method == "search":
            return [1, 2]
        elif method == "name_get":
            return [[1, "Partner One"], [2, "Partner Two"]]
        elif method == "create":
            return 99
        elif method == "write":
            return True
        elif method == "unlink":
            return True
        elif method == "message_post":
            return 101
        elif method == "ir.attachment":
            if method == "search_read":
                return [
                    {"id": 1, "name": "file.pdf", "mimetype": "application/pdf", "res_model": "res.partner", "res_id": 1},
                ]
        return []


@pytest.fixture
def mock_xmlrpc():
    with patch("app.core.odoo_client.xmlrpc.client.ServerProxy", MockServerProxy):
        yield


class TestSchemaIntegration:
    def test_schema_models(self, mock_xmlrpc):
        response = client.post("/schema/models", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "query": "partner",
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "records" in data

    def test_schema_fields(self, mock_xmlrpc):
        response = client.post("/schema/fields", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert "fields" in data
        assert "name" in data["fields"]


class TestRecordsIntegration:
    def test_search_read(self, mock_xmlrpc):
        response = client.post("/records/search-read", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
            "domain": [],
            "limit": 5,
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "records" in data
        assert len(data["records"]) == 2
        assert data["records"][0]["name"] == "Partner One"

    def test_count(self, mock_xmlrpc):
        response = client.post("/records/count", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2

    def test_read(self, mock_xmlrpc):
        response = client.post("/records/read", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
            "ids": [1],
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "records" in data
        assert data["records"][0]["id"] == 1


class TestExecuteKwIntegration:
    def test_execute_kw_allowed(self, mock_xmlrpc):
        from app.core.config import get_settings
        os.environ["EXECUTE_KW_ALLOW_WRITE"] = "true"
        os.environ["EXECUTE_KW_ALLOW_WRITE_METHODS"] = "true"
        os.environ["DEBUG"] = "true"
        get_settings.cache_clear()
        response = client.post("/execute-kw/", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
            "method": "search",
            "args": [[]],
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200

    def test_execute_kw_blocked_method(self, mock_xmlrpc):
        os.environ["EXECUTE_KW_BLOCKED_METHODS"] = "unlink,sudo"
        response = client.post("/execute-kw/", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
            "method": "unlink",
            "args": [[1]],
        }, headers=AUTH_HEADERS)
        assert response.status_code == 403
        os.environ["EXECUTE_KW_BLOCKED_METHODS"] = ""


class TestMessagesIntegration:
    def test_create_message(self, mock_xmlrpc):
        response = client.post("/messages/create", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
            "record_id": 1,
            "body": "Test message",
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == 101

    @pytest.mark.skip(reason="Intermittent ExceptionGroup in CI - middleware issue")
    def test_list_messages(self, mock_xmlrpc):
        response = client.post("/messages/list", json={
            "credentials": {
                "url": "https://odoo.example.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            },
            "model": "res.partner",
            "record_id": 1,
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
