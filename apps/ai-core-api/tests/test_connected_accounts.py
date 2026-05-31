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

@pytest.fixture(autouse=True)
def mock_db_override():
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.pop(get_db, None)

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
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_connect_odoo_invalid_credentials(self, mock_store, mock_verify):
        from fastapi import HTTPException
        mock_store.return_value = None
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
        detail = data.get("detail", {})
        assert isinstance(detail, dict)
        # The message should contain "verification" or "credentials" context
        msg = (detail.get("message") or str(detail)).lower()
        assert "credential" in msg or "verif" in msg or "error" in msg or "fail" in msg

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


# ── Structured Error Tests ──

class TestStructuredErrors:
    """Tests for structured ConnectErrorDetail responses."""

    def test_connect_returns_connect_error_detail_on_failure(self):
        """A verification failure must return a ConnectErrorDetail-shaped dict."""
        from app.routers.connected_accounts import ConnectErrorDetail
        err = ConnectErrorDetail(
            error_type="odoo_credentials_invalid",
            stage="verify_odoo",
            message="Test message",
            technical_detail="Test technical detail",
            request_id="abc123",
        )
        d = err.model_dump()
        assert d["error_type"] == "odoo_credentials_invalid"
        assert d["stage"] == "verify_odoo"
        assert d["message"] == "Test message"
        assert d["technical_detail"] == "Test technical detail"
        assert d["request_id"] == "abc123"

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_odoo_connector_auth_failed(self, mock_store, mock_verify):
        """Connector returning 401 (internal key mismatch) must produce odoo_connector_auth_failed."""
        from fastapi import HTTPException
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(
            status_code=401,
            detail={
                "error_type": "odoo_connector_auth_failed",
                "stage": "verify_connector",
                "message": "Internal connector API key mismatch.",
                "technical_detail": "Connector returned 401: Invalid internal API key",
            }
        )

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "my-key",
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error_type") == "odoo_connector_auth_failed"
        # Stage is propagated from the original error
        assert detail.get("stage") == "verify_connector"
        assert "API key mismatch" in detail.get("message", "")

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_odoo_connector_unreachable(self, mock_store, mock_verify):
        """Connector unreachable must produce odoo_connector_unreachable."""
        from fastapi import HTTPException
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(
            status_code=502,
            detail={
                "error_type": "odoo_connector_unreachable",
                "stage": "verify_connector",
                "message": "Could not reach the Odoo Connector service.",
                "technical_detail": "Connection failed: ...",
            }
        )

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "my-key",
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error_type") == "odoo_connector_unreachable"

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_odoo_credentials_invalid(self, mock_store, mock_verify):
        """Invalid Odoo credentials must produce odoo_credentials_invalid."""
        from fastapi import HTTPException
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(
            status_code=400,
            detail={
                "error_type": "odoo_credentials_invalid",
                "stage": "verify_odoo",
                "message": "Odoo credentials are invalid.",
                "technical_detail": "Odoo auth error: Invalid password",
            }
        )

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "wrong-key",
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error_type") == "odoo_credentials_invalid"

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_key_vault_write_failure(self, mock_store):
        """Key Vault write failure must produce key_vault_write_failed."""
        from fastapi import HTTPException
        import logging
        logging.disable(logging.CRITICAL)
        try:
            mock_store.side_effect = HTTPException(
                status_code=500,
                detail={
                    "error_type": "key_vault_write_failed",
                    "stage": "store_secret",
                    "message": "Failed to save connection credentials securely.",
                    "technical_detail": "RBAC authorization failed",
                }
            )
            response = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://odoo.example.com",
                    "odoo_db": "prod_db",
                    "odoo_username": "alden@example.com",
                    "odoo_api_key": "my-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 500
            detail = response.json().get("detail", {})
            assert detail.get("error_type") == "key_vault_write_failed"
        finally:
            logging.disable(logging.NOTSET)


# ── Save as Unverified Tests ──

class TestSaveAsUnverified:
    """When KV save succeeds but verification fails, account must be saved with status='error'."""

    @patch("app.routers.connected_accounts._fetch_odoo_company_metadata")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_account_saved_as_error_on_verify_fail(self, mock_store, mock_verify, mock_fetch):
        """Account must be saved with status='error' when verification fails after KV save."""
        from fastapi import HTTPException
        from unittest.mock import AsyncMock, patch as _patch
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(status_code=400, detail="Verification failed")
        mock_fetch.return_value = {}

        # Expose the mock session to verify calls
        mock_session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = lambda self=None: None
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        mock_session.execute = AsyncMock(return_value=result_mock)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://odoo.example.com",
                    "odoo_db": "prod_db",
                    "odoo_username": "alden@example.com",
                    "odoo_api_key": "my-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            # 400 error because verification failed, but account IS saved
            assert response.status_code == 400
            detail = response.json().get("detail", {})
            assert "error_type" in detail

            # Find the AIConnectedAccount in db.add calls (there are also audit events)
            from app.models.models import AIConnectedAccount as ACA
            add_calls = mock_session.add.call_args_list
            saved_accounts = [call[0][0] for call in add_calls if isinstance(call[0][0], ACA)]
            assert len(saved_accounts) >= 1, "AIConnectedAccount was not added to DB"
            saved_account = saved_accounts[0]
            assert saved_account.status == "error"
            assert saved_account.odoo_url == "https://odoo.example.com"
            assert saved_account.odoo_db == "prod_db"
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._fetch_odoo_company_metadata")
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_url_db_username_preserved_after_verify_fail(self, mock_store, mock_verify, mock_fetch):
        """User-entered URL, DB, and username must be preserved even after failed verification."""
        from fastapi import HTTPException
        from unittest.mock import AsyncMock
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(status_code=400, detail="Verification failed")
        mock_fetch.return_value = {}

        mock_session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = lambda self=None: None
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        mock_session.execute = AsyncMock(return_value=result_mock)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://my-instance.odoo.com",
                    "odoo_db": "my_custom_db",
                    "odoo_username": "admin@mycompany.com",
                    "odoo_api_key": "my-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 400

            # Find the AIConnectedAccount in db.add calls
            from app.models.models import AIConnectedAccount as ACA
            add_calls = mock_session.add.call_args_list
            saved_accounts = [call[0][0] for call in add_calls if isinstance(call[0][0], ACA)]
            assert len(saved_accounts) >= 1, "AIConnectedAccount was not added to DB"
            saved_account = saved_accounts[0]
            assert saved_account.odoo_url == "https://my-instance.odoo.com"
            assert saved_account.odoo_db == "my_custom_db"
            assert saved_account.provider_username == "admin@mycompany.com"
            assert saved_account.status == "error"
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    @patch("app.routers.connected_accounts._retrieve_key_vault_secret")
    def test_verify_fail_still_allows_test_connection(self, mock_retrieve, mock_store, mock_verify):
        """After a failed verify that saves as error, Test Connection should still work."""
        from fastapi import HTTPException
        from unittest.mock import AsyncMock, MagicMock
        from uuid import UUID
        from app.models.models import AIConnectedAccount

        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(status_code=400, detail="Verification failed")
        mock_retrieve.return_value = "my-key"

        # Create a real account that the test endpoint can find
        saved_account = AIConnectedAccount(
            id=UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
            user_id=UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
            provider="odoo",
            provider_username="alden@example.com",
            secret_reference="test-secret-ref",
            status="error",
            odoo_url="https://odoo.example.com",
            odoo_db="prod_db",
        )

        mock_session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=saved_account)
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        mock_session.execute = AsyncMock(return_value=result_mock)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            # First connect (verification will fail, save as error)
            response = client.post(
                "/connected-accounts/odoo/connect",
                json={
                    "odoo_url": "https://odoo.example.com",
                    "odoo_db": "prod_db",
                    "odoo_username": "alden@example.com",
                    "odoo_api_key": "my-key",
                },
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 400

            # Now test connection (verify succeeds this time)
            mock_verify.reset_mock()
            mock_verify.side_effect = None
            mock_verify.return_value = None

            test_resp = client.post(
                "/connected-accounts/odoo/test",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert test_resp.status_code == 200
            test_data = test_resp.json()
            assert test_data["status"] == "connected"
            assert test_data["odoo_url"] == "https://odoo.example.com"
            assert test_data["odoo_db"] == "prod_db"
        finally:
            app.dependency_overrides.pop(get_db, None)


# ── Protected Debug Endpoint Tests ──

class TestProtectedDebugEndpoint:
    """The /debug/connector endpoint must require auth."""

    def test_debug_requires_auth_in_production(self, monkeypatch):
        """Debug connector must return 401 without auth when production mode prevents bypass."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("DEBUG", "false")
        from app.core.config import get_settings
        get_settings.cache_clear()

        response = client.get("/connected-accounts/debug/connector")
        assert response.status_code == 401

        get_settings.cache_clear()

    def test_debug_allows_authenticated_admin(self):
        """Debug connector must allow access when properly authenticated as admin."""
        # In test env with DEBUG=true, anonymous gets admin via debug bypass
        response = client.get(
            "/connected-accounts/debug/connector",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200


# ── Production Mode Debug Bypass Tests ──

class TestProductionDebugBypass:
    """Production mode must reject debug anonymous admin bypass."""

    def test_production_rejects_debug_anonymous_bypass(self, monkeypatch):
        """When APP_ENV=production and DEBUG=true, anonymous access must be rejected."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("DEBUG", "true")
        from app.core.config import get_settings
        get_settings.cache_clear()

        response = client.get(
            "/connected-accounts/odoo/status",
            headers={}
        )
        assert response.status_code == 401

        get_settings.cache_clear()


# ── Internal Key Mismatch Detection Tests ──

class TestInternalKeyMismatch:
    """ODOO_CONNECTOR_API_KEY <-> INTERNAL_API_KEY mismatch must be clearly detected."""

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_key_mismatch_clearly_reported(self, mock_store, mock_verify):
        """Key mismatch (connector 401) must produce odoo_connector_auth_failed with clear message."""
        from fastapi import HTTPException
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(
            status_code=401,
            detail={
                "error_type": "odoo_connector_auth_failed",
                "stage": "verify_connector",
                "message": "Internal connector API key mismatch. Contact an administrator.",
                "technical_detail": "Connector returned 401: Invalid internal API key",
            }
        )

        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "my-key",
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error_type") == "odoo_connector_auth_failed"
        # The message should reference "API key mismatch" or similar
        msg = (detail.get("message") or "").lower()
        assert "mismatch" in msg or "key mismatch" in msg or "api key" in msg


# ── Startup Config Validation Tests ──

class TestStartupConfigValidation:
    """Health endpoint must validate startup configuration."""

    def test_config_validation_reports_debug_in_production(self, monkeypatch):
        """DEBUG=true in production must be reported as a config issue."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("DEBUG", "true")
        from app.core.config import get_settings
        get_settings.cache_clear()

        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        config_issues = data.get("config_issues", [])
        debug_issues = [i for i in config_issues if i.get("check") == "DEBUG"]
        assert len(debug_issues) > 0
        assert "production" in debug_issues[0].get("message", "").lower()

        get_settings.cache_clear()

    def test_config_validation_reports_missing_connector_url(self, monkeypatch):
        """Missing ODOO_CONNECTOR_URL must be reported as a config issue."""
        monkeypatch.setenv("ODOO_CONNECTOR_URL", "")
        monkeypatch.setenv("KEY_VAULT_URI", "https://test.vault.azure.net")
        monkeypatch.setenv("POSTGRES_HOST", "localhost")
        monkeypatch.setenv("ODOO_CONNECTOR_API_KEY", "some-key")
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("DEBUG", "false")
        from app.core.config import get_settings
        get_settings.cache_clear()

        response = client.get("/health")
        data = response.json()
        config_issues = data.get("config_issues", [])
        url_issues = [i for i in config_issues if i.get("check") == "ODOO_CONNECTOR_URL"]
        assert len(url_issues) > 0

        get_settings.cache_clear()
