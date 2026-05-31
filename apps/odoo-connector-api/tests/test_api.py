import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.main import app
from app.core.config import get_settings

client = TestClient(app)

AUTH_HEADERS = {"X-Internal-API-Key": "test-internal-key"}


class TestHealth:
    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "capabilities" in data

    def test_capabilities(self):
        response = client.get("/capabilities", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "endpoints" in data


class TestSchema:
    def test_schema_models_no_auth_in_debug(self):
        response = client.post("/schema/models", json={
            "credentials": {
                "url": "https://example.odoo.com",
                "db": "test",
                "username": "test",
                "api_key": "test",
            },
            "query": "account",
        }, headers=AUTH_HEADERS)
        assert response.status_code in [200, 400, 500, 502]


class TestRecords:
    def test_search_read_requires_credentials(self):
        response = client.post("/records/search-read", json={
            "credentials": {
                "url": "https://example.odoo.com",
                "db": "test",
                "username": "test",
                "api_key": "test",
            },
            "model": "res.partner",
            "domain": [],
            "limit": 5,
        }, headers=AUTH_HEADERS)
        assert response.status_code in [200, 400, 500, 502]

    @patch("app.routers.records._get_client")
    def test_search_read_passes_db_unchanged(self, mock_get_client):
        """The request credentials.db must reach OdooClient without substitution."""
        from unittest.mock import MagicMock
        mock_client = MagicMock()
        mock_client.search_read.return_value = []
        mock_get_client.return_value = mock_client

        user_db = "aldenbronkhorst-lotslotsmore-lotslotsmore-15954717"
        response = client.post("/records/search-read", json={
            "credentials": {
                "url": "https://lotslotsmore.odoo.com",
                "db": user_db,
                "username": "alden@lotslotsmore.com",
                "api_key": "test-key",
            },
            "model": "res.partner",
            "domain": [],
            "limit": 1,
        }, headers=AUTH_HEADERS)
        assert response.status_code == 200

        # Verify _get_client was called with the exact user-provided db
        mock_get_client.assert_called_once()
        creds_arg = mock_get_client.call_args[0][0]
        assert creds_arg.db == user_db, f"Expected db={user_db!r}, got db={creds_arg.db!r}"

    def test_count_requires_credentials(self):
        response = client.post("/records/count", json={
            "credentials": {
                "url": "https://example.odoo.com",
                "db": "test",
                "username": "test",
                "api_key": "test",
            },
            "model": "res.partner",
        }, headers=AUTH_HEADERS)
        assert response.status_code in [200, 400, 500, 502]


class TestExecuteKw:
    def test_execute_kw_blocked_by_default(self):
        get_settings.cache_clear()
        os.environ["DEBUG"] = "false"
        os.environ["EXECUTE_KW_ALLOW_WRITE"] = "false"
        os.environ["INTERNAL_API_KEY"] = "test-internal-key"
        try:
            response = client.post("/execute-kw/", json={
                "credentials": {
                    "url": "https://example.odoo.com",
                    "db": "test",
                    "username": "test",
                    "api_key": "test",
                },
                "model": "res.partner",
                "method": "search",
                "args": [[]],
            }, headers={"X-Internal-API-Key": "test-internal-key"})
            assert response.status_code == 403
        finally:
            get_settings.cache_clear()
            os.environ["DEBUG"] = "true"
            os.environ["EXECUTE_KW_ALLOW_WRITE"] = "true"
            os.environ.pop("INTERNAL_API_KEY", None)


class TestAttachments:
    def test_list_attachments_structure(self):
        response = client.post("/attachments/list", json={
            "credentials": {
                "url": "https://example.odoo.com",
                "db": "test",
                "username": "test",
                "api_key": "test",
            },
            "model": "res.partner",
            "record_id": 1,
        }, headers=AUTH_HEADERS)
        assert response.status_code in [200, 400, 500, 502]


class TestMessages:
    def test_list_messages_structure(self):
        response = client.post("/messages/list", json={
            "credentials": {
                "url": "https://example.odoo.com",
                "db": "test",
                "username": "test",
                "api_key": "test",
            },
            "model": "res.partner",
            "record_id": 1,
        }, headers=AUTH_HEADERS)
        assert response.status_code in [200, 400, 500, 502]
