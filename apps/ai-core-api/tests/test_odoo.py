import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

# Enable debug mode for tests
os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.main import app

# Mock the database completely
from app.core.database import get_db

async def mock_get_db():
    """Return a mock DB session"""
    mock_session = AsyncMock()
    
    # Mock execute().scalar_one_or_none() chain for connected account lookup
    mock_result = AsyncMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    
    yield mock_session


app.dependency_overrides[get_db] = mock_get_db


@pytest.fixture
def client():
    return TestClient(app)


class TestMissingCredentials:
    """Test 8: AI Core API blocks missing user credentials"""
    
    def test_search_read_blocks_no_account(self, client):
        response = client.post("/tools/odoo/search-read", json={
            "model": "res.partner",
            "limit": 5,
        })
        assert response.status_code == 403
        data = response.json()
        assert "No Odoo connected account found" in data["detail"]
    
    def test_execute_blocks_no_account(self, client):
        response = client.post("/tools/odoo/execute", json={
            "model": "res.partner",
            "method": "search",
            "args": [[]],
        })
        assert response.status_code == 403
        data = response.json()
        assert "No Odoo connected account found" in data["detail"]
    
    def test_messages_create_blocks_no_account(self, client):
        response = client.post("/tools/odoo/messages/create", json={
            "model": "res.partner",
            "record_id": 1,
            "body": "Test message",
        })
        assert response.status_code == 403
        data = response.json()
        assert "No Odoo connected account found" in data["detail"]


class TestExecuteKwGating:
    """Test 11: execute_kw write/custom methods are gated at AI Core level"""
    
    def test_blocks_unlink(self, client):
        response = client.post("/tools/odoo/execute", json={
            "model": "res.partner",
            "method": "unlink",
            "args": [[1]],
        })
        assert response.status_code == 403
        data = response.json()
        assert "blocked" in data["detail"].lower() or "Method 'unlink' is blocked" in data["detail"]
    
    def test_blocks_sudo(self, client):
        response = client.post("/tools/odoo/execute", json={
            "model": "res.partner",
            "method": "sudo",
            "args": [],
        })
        assert response.status_code == 403
        assert "Method 'sudo' is blocked" in response.json()["detail"]
    
    def test_blocks_write_without_mode(self, client):
        response = client.post("/tools/odoo/execute", json={
            "model": "res.partner",
            "method": "write",
            "args": [[1], {"name": "New Name"}],
        })
        assert response.status_code == 403
        assert "Write method 'write' is blocked" in response.json()["detail"]
    
    def test_allows_read_methods(self, client):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            mock_call.return_value = {"result": [[1, "Partner One"]]}
            
            response = client.post("/tools/odoo/execute", json={
                "model": "res.partner",
                "method": "search",
                "args": [[]],
            })
            assert response.status_code == 200
            mock_call.assert_called_once()


class TestServiceAccount:
    """Test service account mode for automation"""
    
    def test_service_account_missing_config(self, client):
        # Clear service account env vars
        for key in ["ODOO_SERVICE_URL", "ODOO_SERVICE_DB", "ODOO_SERVICE_USERNAME", "ODOO_SERVICE_API_KEY"]:
            os.environ.pop(key, None)
        
        response = client.post("/tools/odoo/search-read", json={
            "model": "res.partner",
            "limit": 5,
            "identity_mode": "service-account",
        })
        assert response.status_code == 500
        assert "Service account credentials not configured" in response.json()["detail"]


class TestConnectorErrorHandling:
    """Test 4: Connector handles Odoo errors cleanly"""
    
    def test_connector_error_propagated(self, client):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            from fastapi import HTTPException
            mock_call.side_effect = HTTPException(status_code=502, detail="Odoo transport error")
            
            response = client.post("/tools/odoo/search-read", json={
                "model": "res.partner",
                "limit": 5,
            })
            assert response.status_code == 502
            assert "Odoo transport error" in response.json()["detail"]
    
    def test_connector_auth_error(self, client):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            from fastapi import HTTPException
            mock_call.side_effect = HTTPException(status_code=401, detail="Odoo authentication failed")
            
            response = client.post("/tools/odoo/search-read", json={
                "model": "res.partner",
                "limit": 5,
            })
            assert response.status_code == 401
            assert "Odoo authentication failed" in response.json()["detail"]


class TestIdentityModeTracking:
    """Test identity_mode is tracked in audit and forwarded to connector"""
    
    def test_user_delegated_mode(self, client):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            mock_call.return_value = {"records": []}
            
            response = client.post("/tools/odoo/search-read", json={
                "model": "res.partner",
                "limit": 5,
                "identity_mode": "user-delegated",
            })
            assert response.status_code == 200
            
            call_args = mock_call.call_args
            payload = call_args[1]["json"]
            assert payload["identity_mode"] == "user-delegated"
    
    def test_target_environment_forwarded(self, client):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            mock_call.return_value = {"records": []}
            
            response = client.post("/tools/odoo/search-read", json={
                "model": "res.partner",
                "limit": 5,
                "target_environment": "production",
                "operation_mode": "read-only",
            })
            assert response.status_code == 200
            
            call_args = mock_call.call_args
            payload = call_args[1]["json"]
            assert payload["target_environment"] == "production"
            assert payload["operation_mode"] == "read-only"
