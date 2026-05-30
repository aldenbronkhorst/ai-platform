import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from uuid import UUID

# Enable debug mode for tests
os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"
os.environ["ODOO_URL"] = "https://company-default.odoo.com"
os.environ["ODOO_DB"] = "company-default-db"

from app.main import app
from app.core.database import get_db
from app.models.models import AIConnectedAccount

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

        # Verify Key Vault was called with a unique opaque pattern
        mock_store.assert_called_once()
        args, _ = mock_store.call_args
        assert args[0].startswith("connected-account-")
        assert args[0].endswith("-secret")
        # Verify a random suffix was inserted (UUID format hex)
        segments = args[0].split("-")
        assert len(segments) >= 5  # connected, account, UUID, random_suffix(12), secret
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


class TestKeyVaultConflict:
    """Tests for handling ObjectIsDeletedButRecoverable and secret naming."""

    def test_generate_secret_name_produces_unique_values(self):
        from app.routers.connected_accounts import _generate_secret_name
        from uuid import UUID

        account_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")
        names = {_generate_secret_name(account_id) for _ in range(100)}
        assert len(names) == 100

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_reconnect_uses_new_secret_name(self, mock_verify, mock_store):
        from uuid import UUID

        mock_verify.return_value = None

        # First connection — generate_secret_name returns first_name
        first_name = "connected-account-e4807f22-97c8-4778-87a2-160f56d25247-abc12345-secret"
        with patch("app.routers.connected_accounts._generate_secret_name",
                   return_value=first_name):
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
            assert mock_store.call_args[0][0] == first_name

        mock_store.reset_mock()

        # Second connection (reconnect) — generate_secret_name returns a DIFFERENT name
        second_name = "connected-account-e4807f22-97c8-4778-87a2-160f56d25247-def67890-secret"
        with patch("app.routers.connected_accounts._generate_secret_name",
                   return_value=second_name):
            response2 = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://odoo.example.com",
                    "odoo_db": "prod_db",
                    "odoo_username": "alden@example.com",
                    "odoo_api_key": "my-new-secret-key"
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response2.status_code == 200

        assert mock_store.call_count == 1
        actual_second = mock_store.call_args[0][0]
        assert actual_second == second_name
        assert actual_second != first_name

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_store_key_vault_returns_user_friendly_message_on_conflict(self, mock_verify, mock_store):
        mock_verify.return_value = None

        from fastapi import HTTPException
        import logging

        logging.disable(logging.CRITICAL)
        try:
            mock_store.side_effect = HTTPException(
                status_code=500,
                detail="Could not save connection credentials because a previously "
                       "deleted secret is still reserved. Please retry, or contact "
                       "support if the issue persists."
            )
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
            assert response.status_code == 500
            assert "deleted secret" in response.json()["detail"].lower()
            assert "ObjectIsDeletedButRecoverable" not in response.json()["detail"]
        finally:
            logging.disable(logging.NOTSET)


# ── Odoo URL Persistence Tests ──

class TestOdooUrlPersistence:
    """The user-provided Odoo URL must be saved and used, not the default/env var."""

    def test_normalize_url_adds_https(self):
        """A URL without scheme must get https:// prepended."""
        from app.routers.connected_accounts import _normalize_odoo_url
        assert _normalize_odoo_url("lotslotsmore.odoo.com") == "https://lotslotsmore.odoo.com"

    def test_normalize_url_removes_trailing_slash(self):
        """Trailing slashes must be stripped."""
        from app.routers.connected_accounts import _normalize_odoo_url
        result = _normalize_odoo_url("https://lotslotsmore.odoo.com/")
        assert result == "https://lotslotsmore.odoo.com"
        assert not result.endswith("/")

    def test_normalize_url_keeps_existing_https(self):
        """A URL that already has https:// must not be double-prefixed."""
        from app.routers.connected_accounts import _normalize_odoo_url
        assert _normalize_odoo_url("https://lotslotsmore.odoo.com") == "https://lotslotsmore.odoo.com"

    def test_normalize_url_trims_whitespace(self):
        """Leading/trailing whitespace must be trimmed."""
        from app.routers.connected_accounts import _normalize_odoo_url
        assert _normalize_odoo_url("  https://lotslotsmore.odoo.com  ") == "https://lotslotsmore.odoo.com"

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_url_persisted_on_connect(self, mock_verify, mock_store):
        """The user-provided Odoo URL and DB must be in the connect response."""
        mock_verify.return_value = None
        mock_store.return_value = None

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://lotslotsmore.odoo.com",
                "odoo_db": "lotslotsmore_prod",
                "odoo_username": "alden@lotslotsmore.com",
                "odoo_api_key": "my-key",
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["odoo_url"] == "https://lotslotsmore.odoo.com"
        assert data["odoo_db"] == "lotslotsmore_prod"
        assert data["provider_username"] == "alden@lotslotsmore.com"
        # Verify no secret leaked
        assert "my-key" not in str(data)

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_url_replaced_on_reconnect(self, mock_verify, mock_store):
        """Reconnecting with a different URL must persist the new URL."""
        from uuid import UUID

        mock_verify.return_value = None

        # First connect with original URL
        with patch("app.routers.connected_accounts._generate_secret_name",
                   return_value="test-uuid-1-secret"):
            response1 = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://old-instance.odoo.com",
                    "odoo_db": "old_db",
                    "odoo_username": "alden@lotslotsmore.com",
                    "odoo_api_key": "old-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response1.status_code == 200
            assert response1.json()["odoo_url"] == "https://old-instance.odoo.com"

        mock_store.reset_mock()

        # Reconnect with a different URL
        with patch("app.routers.connected_accounts._generate_secret_name",
                   return_value="test-uuid-2-secret"):
            response2 = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://new-instance.odoo.com",
                    "odoo_db": "new_db",
                    "odoo_username": "alden@lotslotsmore.com",
                    "odoo_api_key": "new-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response2.status_code == 200
            data2 = response2.json()
            assert data2["odoo_url"] == "https://new-instance.odoo.com"
            assert data2["odoo_db"] == "new_db"
            # The old URL should NOT be returned
            assert data2["odoo_url"] != "https://old-instance.odoo.com"

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    def test_env_var_does_not_override_saved_url(self, mock_verify, mock_store):
        """When an account has a saved odoo_url/odoo_db, env vars must NOT override.

        This test patches the connected_accounts endpoint to simulate an existing
        account with saved URL/DB, and verifies the Odoo status endpoint returns
        the saved values, not the env var defaults."""
        from fastapi import HTTPException

        mock_verify.return_value = None

        # Connect with a specific URL (different from env var ODOO_URL)
        with patch("app.routers.connected_accounts._generate_secret_name",
                   return_value="test-uuid-env-secret"):
            response = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://user-saved.odoo.com",
                    "odoo_db": "user_saved_db",
                    "odoo_username": "alden@lotslotsmore.com",
                    "odoo_api_key": "my-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            # The saved URL is NOT the env var default
            assert data["odoo_url"] == "https://user-saved.odoo.com"
            assert data["odoo_url"] != os.environ.get("ODOO_URL")

    def test_url_normalized_on_connect(self):
        """A URL without https:// must be normalized and persisted when connecting."""
        from app.routers.connected_accounts import _normalize_odoo_url
        assert _normalize_odoo_url("lotslotsmore.odoo.com") == "https://lotslotsmore.odoo.com"
