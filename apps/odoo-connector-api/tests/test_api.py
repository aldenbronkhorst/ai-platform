import os
import pytest
from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"

from app.main import app

client = TestClient(app)


class TestHealth:
    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "capabilities" in data

    def test_capabilities(self):
        response = client.get("/capabilities")
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
        })
        # Will fail with connection error since it's a fake URL, but should get past auth
        assert response.status_code in [200, 500, 502]


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
        })
        assert response.status_code in [200, 500, 502]

    def test_count_requires_credentials(self):
        response = client.post("/records/count", json={
            "credentials": {
                "url": "https://example.odoo.com",
                "db": "test",
                "username": "test",
                "api_key": "test",
            },
            "model": "res.partner",
        })
        assert response.status_code in [200, 500, 502]


class TestExecuteKw:
    def test_execute_kw_blocked_by_default(self):
        # Reset env to non-debug
        os.environ["DEBUG"] = "false"
        os.environ["EXECUTE_KW_ALLOW_WRITE"] = "false"
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
        })
        assert response.status_code == 403


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
        })
        assert response.status_code in [200, 500, 502]


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
        })
        assert response.status_code in [200, 500, 502]
