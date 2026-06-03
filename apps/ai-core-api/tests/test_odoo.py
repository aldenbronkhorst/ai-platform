import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

# Enable debug mode for tests
os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.main import app
from app.core.database import get_db

# Mock DB dependency completely
async def mock_get_db():
    session = AsyncMock()
    # Make execute() return a mock result with scalar_one_or_none as sync method
    result_mock = AsyncMock()
    result_mock.scalar_one_or_none = lambda self=None: None
    result_mock.scalars = lambda self=None: result_mock
    result_mock.all = lambda self=None: []
    session.add = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)
    yield session

@pytest.fixture(autouse=True)
def mock_db_override():
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.pop(get_db, None)

client = TestClient(app)


class TestMissingCredentials:
    """Test 8: AI Core API blocks missing user credentials"""
    
    def test_search_read_blocks_no_account(self):
        response = client.post("/tools/odoo/search-read", json={
            "model": "res.partner",
            "limit": 5,
        })
        assert response.status_code == 403
        data = response.json()
        assert "connected account" in data["detail"].lower()
    
    def test_execute_blocks_no_account(self):
        response = client.post("/tools/odoo/execute", json={
            "model": "res.partner",
            "method": "search",
            "args": [[]],
        })
        assert response.status_code == 403
        data = response.json()
        assert "connected account" in data["detail"].lower()
    
    def test_messages_create_blocks_no_account(self):
        response = client.post("/tools/odoo/messages/create", json={
            "model": "res.partner",
            "record_id": 1,
            "body": "Test message",
        })
        assert response.status_code == 403
        data = response.json()
        assert "connected account" in data["detail"].lower()


class TestExecuteKwPassThrough:
    """execute_kw authorization is delegated to the user's Odoo account."""
    
    def test_unlink_passes_to_connector_when_user_has_account(self):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            mock_call.return_value = {"result": True}

            response = client.post("/tools/odoo/execute", json={
                "model": "res.partner",
                "method": "unlink",
                "args": [[1]],
            })
            assert response.status_code == 200
            mock_call.assert_called_once()
    
    def test_custom_method_passes_to_connector_when_user_has_account(self):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            mock_call.return_value = {"result": []}

            response = client.post("/tools/odoo/execute", json={
                "model": "res.partner",
                "method": "custom_server_method",
                "args": [],
            })
            assert response.status_code == 200
            mock_call.assert_called_once()
    
    def test_write_passes_to_connector_without_platform_operation_mode(self):
        with patch("app.routers.odoo._resolve_odoo_credentials") as mock_creds, \
             patch("app.routers.odoo._call_connector") as mock_call:
            mock_creds.return_value = {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret",
            }
            mock_call.return_value = {"result": True}

            response = client.post("/tools/odoo/execute", json={
                "model": "res.partner",
                "method": "write",
                "args": [[1], {"name": "New Name"}],
            })
            assert response.status_code == 200
            mock_call.assert_called_once()
    
    def test_allows_read_methods(self):
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
    
    def test_service_account_missing_config(self):
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
    
    def test_connector_error_propagated(self):
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
    
    def test_connector_auth_error(self):
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
    
    def test_user_delegated_mode(self):
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
            
            # Verify identity_mode was forwarded
            args = mock_call.call_args[0]
            payload = args[2] if len(args) > 2 else {}
            assert payload.get("identity_mode") == "user-delegated"
    
    def test_target_environment_forwarded(self):
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
            
            # Verify target_environment and operation_mode were forwarded
            args = mock_call.call_args[0]
            payload = args[2] if len(args) > 2 else {}
            assert payload.get("target_environment") == "production"
            assert payload.get("operation_mode") == "read-only"
