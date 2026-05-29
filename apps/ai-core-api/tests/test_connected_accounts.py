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
    # Make execute() return a mock result
    result_mock = AsyncMock()
    result_mock.scalar_one_or_none = lambda self=None: None
    result_mock.scalars = lambda self=None: result_mock
    result_mock.all = lambda self=None: []
    session.execute = AsyncMock(return_value=result_mock)
    yield session

app.dependency_overrides[get_db] = mock_get_db

client = TestClient(app)


class TestConnectedAccountsFlow:
    """Tests the full Connected Accounts API flow for Odoo."""

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_connect_odoo_success(self, mock_store, mock_verify):
        mock_verify.return_value = None
        mock_store.return_value = None

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "my-secret-api-key"
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "odoo"
        assert data["provider_username"] == "alden@example.com"
        assert data["status"] == "connected"
        assert data["target_environment"] == "production"
        
        # Verify API key is NOT in response
        assert "odoo_api_key" not in data
        assert "my-secret-api-key" not in str(data)

        # Verify Key Vault was called with opaque pattern
        mock_store.assert_called_once()
        args, _ = mock_store.call_args
        assert args[0].startswith("connected-account-")
        assert args[0].endswith("-secret")
        assert args[1] == "my-secret-api-key"

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_connect_odoo_invalid_credentials(self, mock_verify):
        from fastapi import HTTPException
        mock_verify.side_effect = HTTPException(status_code=400, detail="Odoo verification failed: Invalid password")

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "wrong-key"
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "verification failed" in data["detail"].lower()

    def test_get_connected_accounts_list(self):
        response = client.get(
            "/connected-accounts",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_odoo_status_not_connected(self):
        response = client.get(
            "/connected-accounts/odoo/status",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_connected"

    @patch("app.routers.connected_accounts._retrieve_key_vault_secret")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_test_connection_not_found(self, mock_verify, mock_retrieve):
        response = client.post(
            "/connected-accounts/odoo/test",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_rotate_credentials_not_found(self):
        response = client.post(
            "/connected-accounts/odoo/rotate",
            json={"odoo_api_key": "new-api-key"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_disconnect_not_found(self):
        response = client.post(
            "/connected-accounts/odoo/disconnect",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
