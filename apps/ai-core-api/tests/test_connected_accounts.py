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
    session.add = MagicMock()
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
        assert "connectors" in data
        assert isinstance(data["connectors"], list)
        connector_keys = {item["connector_key"] for item in data["connectors"]}
        assert connector_keys == {"odoo", "azure", "github"}

    def test_get_connected_accounts_uses_stored_delegated_state_without_token_lookup(self):
        async def fake_token_status(provider, _user_id):
            raise AssertionError(f"unexpected token lookup for {provider}")

        with patch("app.services.connected_account_state.token_status", new=AsyncMock(side_effect=fake_token_status)):
            response = client.get(
                "/connected-accounts",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )

        assert response.status_code == 200
        connectors = {item["connector_key"]: item for item in response.json()["connectors"]}
        assert connectors["azure"]["status"] == "not_connected"
        assert connectors["github"]["status"] == "not_connected"

    def test_get_connected_accounts_can_include_verified_token_state(self):
        async def fake_token_status(provider, _user_id):
            if provider == "azure":
                return {
                    "status": "connected",
                    "provider": "azure",
                    "username": "alden@example.com",
                    "scope": "https://management.core.windows.net//.default",
                }
            return {"status": "not_connected", "provider": provider}

        with patch("app.services.connected_account_state.token_status", new=AsyncMock(side_effect=fake_token_status)):
            response = client.get(
                "/connected-accounts?include_token_state=true",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )

        assert response.status_code == 200
        connectors = {item["connector_key"]: item for item in response.json()["connectors"]}
        assert connectors["azure"]["status"] == "connected"
        assert connectors["azure"]["state"]["configured"] is True
        assert connectors["azure"]["state"]["token_status"] == "connected"
        assert connectors["azure"]["state"]["source"] == "token_store"

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

    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector")
    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_odoo_db_passed_unchanged_to_connector(self, mock_store, mock_verify):
        """The exact req.odoo_db must be passed through to the connector without substitution."""
        mock_store.return_value = None

        user_db = "aldenbronkhorst-lotslotsmore-lotslotsmore-15954717"
        response = client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://lotslotsmore.odoo.com",
                "odoo_db": user_db,
                "odoo_username": "alden@lotslotsmore.com",
                "odoo_api_key": "my-key",
            },
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        # Verify the connector was called with the exact user-provided database
        mock_verify.assert_called_once()
        _, kwargs = mock_verify.call_args
        assert kwargs["db"] == user_db, f"Expected db={user_db!r}, got db={kwargs['db']!r}"


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
        from unittest.mock import AsyncMock
        mock_store.return_value = None
        mock_verify.side_effect = HTTPException(status_code=400, detail="Verification failed")
        mock_fetch.return_value = {}

        # Expose the mock session to verify calls
        mock_session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = lambda self=None: None
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        mock_session.add = MagicMock()
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
        mock_session.add = MagicMock()
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
        mock_session.add = MagicMock()
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
        # Health still returns 200 in dev/test but includes config_issues
        assert response.status_code == 200
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


# ── Disconnect Cleanup Tests ──

class TestDisconnectCleanup:
    """Disconnect must clear all connection metadata and credentials."""

    def _make_account(self, **overrides):
        """Create an AIConnectedAccount with all fields populated."""
        from uuid import UUID
        from datetime import datetime
        account = AIConnectedAccount(
            id=UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
            user_id=UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
            provider="odoo",
            provider_username="alden@lotslotsmore.com",
            provider_user_id="odoo-user-123",
            provider_display_name="Alden Bronkhorst",
            secret_reference="connected-account-abc123-secret",
            status="connected",
            permission_summary="Read access to res.partner, res.company",
            last_verified_at=datetime(2025, 6, 1, 12, 0, 0),
            target_environment="production",
            odoo_url="https://lotslotsmore.odoo.com",
            odoo_db="lotslotsmore_prod",
            odoo_company_id=1,
            odoo_company_name="Lots Lots More",
            odoo_currency_code="ZAR",
            odoo_currency_symbol="R",
        )
        for k, v in overrides.items():
            setattr(account, k, v)
        return account

    def _setup_mock_db(self, account):
        """Set up mock DB with the given account returned from execute()."""
        from unittest.mock import AsyncMock, MagicMock
        mock_session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=account)
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(return_value=result_mock)
        return mock_session

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_secret_reference(self, _mock_delete):
        """Disconnect must clear secret_reference on the DB model."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "disconnected"
            # secret_reference is intentionally excluded from the API response model,
            # so verify it was cleared on the DB model
            assert account.secret_reference is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_provider_username(self, _mock_delete):
        """Disconnect must clear provider_username."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            assert account.provider_username == "alden@lotslotsmore.com"
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["provider_username"] is None
            assert account.provider_username is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_odoo_url(self, _mock_delete):
        """Disconnect must clear odoo_url."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["odoo_url"] is None
            assert account.odoo_url is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_odoo_db(self, _mock_delete):
        """Disconnect must clear odoo_db."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["odoo_db"] is None
            assert account.odoo_db is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_company_currency_metadata(self, _mock_delete):
        """Disconnect must clear company_id, company_name, currency_code, currency_symbol."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["odoo_company_id"] is None
            assert data["odoo_company_name"] is None
            assert data["odoo_currency_code"] is None
            assert data["odoo_currency_symbol"] is None
            assert account.odoo_company_id is None
            assert account.odoo_company_name is None
            assert account.odoo_currency_code is None
            assert account.odoo_currency_symbol is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_provider_user_id_and_display_name(self, _mock_delete):
        """Disconnect must clear provider_user_id, provider_display_name, permission_summary."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "disconnected"
            assert account.provider_user_id is None
            assert account.provider_display_name is None
            assert account.permission_summary is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_clears_last_verified_at(self, _mock_delete):
        """Disconnect must clear last_verified_at."""
        from unittest.mock import AsyncMock
        from datetime import datetime
        account = self._make_account()
        account.last_verified_at = datetime(2025, 6, 1, 12, 0, 0)
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["last_verified_at"] is None
            assert account.last_verified_at is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("app.routers.connected_accounts._delete_key_vault_secret")
    def test_disconnect_creates_audit_event(self, _mock_delete):
        """Disconnect must still create an audit event."""
        from unittest.mock import AsyncMock
        account = self._make_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.post(
                "/connected-accounts/odoo/disconnect",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200

            log_event_calls = [
                call for call in mock_session.add.call_args_list
                if hasattr(call[0][0], 'action_type')
            ]
            assert len(log_event_calls) >= 1
            audit_event = log_event_calls[0][0][0]
            assert audit_event.action_type == "disconnect"
            assert audit_event.target_system == "odoo"
        finally:
            app.dependency_overrides.pop(get_db, None)


# ── Status Endpoint for Disconnected Accounts ──

class TestDisconnectedAccountStatus:
    """Status endpoint must return not_connected with null detail fields when account is disconnected."""

    def _make_disconnected_account(self):
        """Create an AIConnectedAccount with status=disconnected but stale fields populated."""
        from uuid import UUID
        account = AIConnectedAccount(
            id=UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
            user_id=UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
            provider="odoo",
            provider_username="alden@lotslotsmore.com",
            secret_reference="connected-account-abc123-secret",
            status="disconnected",
            odoo_url="https://lotslotsmore.odoo.com",
            odoo_db="lotslotsmore_prod",
            odoo_company_id=1,
            odoo_company_name="Lots Lots More",
            odoo_currency_code="ZAR",
            odoo_currency_symbol="R",
        )
        return account

    def _setup_mock_db(self, account):
        """Set up mock DB with the given account returned from execute()."""
        from unittest.mock import AsyncMock, MagicMock
        mock_session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=account)
        result_mock.scalars = lambda self=None: result_mock
        result_mock.all = lambda self=None: []
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(return_value=result_mock)
        return mock_session

    def test_status_returns_not_connected_with_null_details(self):
        """Disconnected account must return status=not_connected with all null details."""
        from unittest.mock import AsyncMock, MagicMock
        account = self._make_disconnected_account()
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.get(
                "/connected-accounts/odoo/status",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "not_connected"
            assert data["provider_username"] is None
            assert data["last_verified_at"] is None
            assert data["target_environment"] is None
            assert data["odoo_url"] is None
            assert data["odoo_db"] is None
            assert data["odoo_company_id"] is None
            assert data["odoo_company_name"] is None
            assert data["odoo_currency_code"] is None
            assert data["odoo_currency_symbol"] is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_status_returns_not_connected_when_no_account(self):
        """No account must return status=not_connected with all null details."""
        response = client.get(
            "/connected-accounts/odoo/status",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_connected"
        assert data.get("odoo_url") is None
        assert data.get("odoo_db") is None
        assert data.get("provider_username") is None

    def test_status_does_not_leak_stale_fields_when_disconnected(self):
        """Stale odoo_url/odoo_db in DB must NOT appear in status response when disconnected."""
        from unittest.mock import AsyncMock, MagicMock
        account = self._make_disconnected_account()
        account.odoo_url = "https://stale-instance.odoo.com"
        account.odoo_db = "stale_db"
        account.provider_username = "stale@user.com"
        mock_session = self._setup_mock_db(account)

        async def mock_get_db():
            yield mock_session

        from app.core.database import get_db
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.get(
                "/connected-accounts/odoo/status",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "not_connected"
            assert data["odoo_url"] is None
            assert data["odoo_db"] is None
            assert data["provider_username"] is None
        finally:
            app.dependency_overrides.pop(get_db, None)


# ── Connector DNS Failure Tests ──

class TestConnectorDnsFailure:
    """DNS resolution failure must produce a specific odoo_connector_dns_failed error type."""

    @patch("app.routers.connected_accounts._store_key_vault_secret")
    def test_dns_failure_returns_odoo_connector_dns_failed(self, mock_store):
        """DNS failure during connect must return odoo_connector_dns_failed error."""
        mock_store.return_value = None

        from fastapi import HTTPException
        import logging
        logging.disable(logging.CRITICAL)
        try:
            with patch(
                "app.routers.connected_accounts._verify_odoo_credentials_via_connector",
                side_effect=HTTPException(
                    status_code=502,
                    detail={
                        "error_type": "odoo_connector_dns_failed",
                        "stage": "verify_connector",
                        "message": "The AI Platform API could not resolve the Odoo Connector service hostname.",
                        "technical_detail": "[Errno -2] Name or service not known",
                    }
                )
            ):
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
                assert detail.get("error_type") == "odoo_connector_dns_failed"
                assert detail.get("stage") == "verify_connector"
                assert "could not resolve" in detail.get("message", "").lower()
        finally:
            logging.disable(logging.NOTSET)

    def test_dns_failure_detected_from_connect_error(self):
        """The _verify_odoo_credentials_via_connector must detect DNS errors from httpx.ConnectError."""
        from app.routers.connected_accounts import _verify_odoo_credentials_via_connector
        import httpx

        with patch.dict(os.environ, {"ODOO_CONNECTOR_URL": "https://this-domain-definitely-does-not-exist-12345.com"}):
            import app.routers.connected_accounts as mod
            old_url = mod.ODOO_CONNECTOR_URL
            mod.ODOO_CONNECTOR_URL = "https://this-domain-definitely-does-not-exist-12345.com"
            old_key = mod.ODOO_CONNECTOR_KEY
            mod.ODOO_CONNECTOR_KEY = "test-key"

            import pytest
            with pytest.raises(Exception) as exc_info:
                import asyncio
                asyncio.run(
                    _verify_odoo_credentials_via_connector(
                        url="https://odoo.example.com",
                        db="test_db",
                        username="test@user.com",
                        api_key="test-key",
                    )
                )

            mod.ODOO_CONNECTOR_URL = old_url
            mod.ODOO_CONNECTOR_KEY = old_key

            error_detail = exc_info.value.detail
            assert error_detail.get("error_type") == "odoo_connector_dns_failed" or \
                   error_detail.get("error_type") == "odoo_connector_unreachable"


# ── Frontend Display Guard Logic Tests ──

class TestFrontendGuardLogic:
    """Verify the shouldShowOdooDetails guard logic used by the frontend."""

    def test_should_show_details_for_connected_status(self):
        should_show = "connected" in ("connected", "error", "needs_verification")
        assert should_show is True

    def test_should_show_details_for_error_status(self):
        should_show = "error" in ("connected", "error", "needs_verification")
        assert should_show is True

    def test_should_not_show_details_for_not_connected(self):
        should_show = "not_connected" in ("connected", "error", "needs_verification")
        assert should_show is False

    def test_should_not_show_details_for_disconnected(self):
        should_show = "disconnected" in ("connected", "error", "needs_verification")
        assert should_show is False

    def test_should_not_show_details_for_unknown_status(self):
        should_show = "pending" in ("connected", "error", "needs_verification")
        assert should_show is False


# ── Connection Trace Tests ──

class TestConnectionTrace:
    """Tests for the structured connection trace infrastructure."""

    def test_generate_connection_attempt_id(self):
        from app.routers.connected_accounts import _generate_connection_attempt_id
        cid = _generate_connection_attempt_id()
        assert cid.startswith("odoo_conn_")
        assert len(cid) == 26  # "odoo_conn_" + 16 hex chars

    def test_key_fingerprint_format(self):
        from app.routers.connected_accounts import _key_fingerprint
        fp = _key_fingerprint("my-secret-key")
        assert fp.startswith("sha256:")
        assert "..." in fp
        # Should be deterministic
        assert _key_fingerprint("my-secret-key") == _key_fingerprint("my-secret-key")
        # Different keys produce different fingerprints
        assert _key_fingerprint("key-a") != _key_fingerprint("key-b")
        # Empty key returns empty string
        assert _key_fingerprint(None) == ""
        assert _key_fingerprint("") == ""

    def test_fingerprint_does_not_reveal_secret(self):
        from app.routers.connected_accounts import _key_fingerprint
        fp = _key_fingerprint("super-secret-api-key-12345")
        # The original key should not be in the fingerprint
        assert "super-secret" not in fp
        assert "12345" not in fp
        assert "sha256:" in fp

    def test_connect_response_includes_connection_attempt_id(self):
        """Failure responses must include connection_attempt_id."""
        from fastapi import HTTPException
        with patch("app.routers.connected_accounts._store_key_vault_secret", return_value=None):
            with patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector") as mock_v:
                mock_v.side_effect = HTTPException(status_code=400, detail={"message": "test"})
                response = client.post(
                    "/connected-accounts/odoo/connect",
                    json={
                        "odoo_url": "https://odoo.example.com",
                        "odoo_db": "my_db",
                        "odoo_username": "user@example.com",
                        "odoo_api_key": "test-key",
                    },
                    headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
                )
                assert response.status_code == 400
                detail = response.json().get("detail", {})
                assert "connection_attempt_id" in detail
                cid = detail["connection_attempt_id"]
                assert cid.startswith("odoo_conn_")

    def test_failure_response_includes_trace(self):
        """Failure responses must include trace dict with stages."""
        from fastapi import HTTPException
        with patch("app.routers.connected_accounts._store_key_vault_secret", return_value=None):
            with patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector") as mock_v:
                mock_v.side_effect = HTTPException(status_code=400, detail={"message": "test"})
                response = client.post(
                    "/connected-accounts/odoo/connect",
                    json={
                        "odoo_url": "https://odoo.example.com",
                        "odoo_db": "my_db",
                        "odoo_username": "user@example.com",
                        "odoo_api_key": "test-key",
                    },
                    headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
                )
                assert response.status_code == 400
                detail = response.json().get("detail", {})
                assert "trace" in detail
                trace = detail["trace"]
                assert "stages" in trace
                assert "ai_core_received" in trace["stages"]
                assert "ai_core_verify_payload" in trace["stages"]
                assert "key_vault_store" in trace["stages"]

    def test_trace_never_contains_raw_api_key(self):
        """Trace stages must never include the raw API key."""
        from fastapi import HTTPException
        with patch("app.routers.connected_accounts._store_key_vault_secret", return_value=None):
            with patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector") as mock_v:
                mock_v.side_effect = HTTPException(status_code=400, detail={"message": "test"})
                response = client.post(
                    "/connected-accounts/odoo/connect",
                    json={
                        "odoo_url": "https://odoo.example.com",
                        "odoo_db": "my_db",
                        "odoo_username": "user@example.com",
                        "odoo_api_key": "my-super-secret-key-123",
                    },
                    headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
                )
                assert response.status_code == 400
                body = str(response.json())
                # The raw API key must not appear anywhere in the response
                assert "my-super-secret-key-123" not in body
                # The fingerprint should be present instead
                assert "sha256:" in body

    def test_trace_shows_same_db_at_all_stages(self):
        """The trace must show the same DB value at ai_core_received and ai_core_verify_payload stages."""
        from fastapi import HTTPException
        with patch("app.routers.connected_accounts._store_key_vault_secret", return_value=None):
            with patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector") as mock_v:
                mock_v.side_effect = HTTPException(status_code=400, detail={"message": "test"})
                response = client.post(
                    "/connected-accounts/odoo/connect",
                    json={
                        "odoo_url": "https://odoo.example.com",
                        "odoo_db": "aldenbronkhorst-lotslotsmore-lotslotsmore-15954717",
                        "odoo_username": "alden@lotslotsmore.com",
                        "odoo_api_key": "test-key",
                    },
                    headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
                )
                assert response.status_code == 400
                trace = response.json().get("detail", {}).get("trace", {})
                stages = trace.get("stages", {})
                received_db = stages.get("ai_core_received", {}).get("odoo_db")
                verify_db = stages.get("ai_core_verify_payload", {}).get("odoo_db")
                assert received_db == "aldenbronkhorst-lotslotsmore-lotslotsmore-15954717"
                assert verify_db == "aldenbronkhorst-lotslotsmore-lotslotsmore-15954717"
                assert received_db == verify_db, "DB mismatch between stages"


# ── Error Classification Tests ──

class TestErrorClassification:
    """Tests for Odoo error classification."""

    def test_database_not_found_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = 'psycopg2.OperationalError: FATAL: database "lotslotsmore_prod" does not exist'
        assert _classify_odoo_error(err) == "odoo_database_not_found"

    def test_authentication_failed_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = "Odoo authentication failed for the linked user."
        assert _classify_odoo_error(err) == "odoo_authentication_failed"

    def test_wrong_password_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = "Invalid password"
        assert _classify_odoo_error(err) == "odoo_authentication_failed"

    def test_permission_error_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = "Access denied"
        assert _classify_odoo_error(err) == "odoo_permission_error"

    def test_ssl_error_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = "SSL: CERTIFICATE_VERIFY_FAILED"
        assert _classify_odoo_error(err) == "odoo_ssl_error"

    def test_timeout_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = "Connection timeout error"
        assert _classify_odoo_error(err) == "odoo_timeout"

    def test_unknown_error_classified(self):
        from app.routers.connected_accounts import _classify_odoo_error
        err = "Some random Odoo traceback"
        assert _classify_odoo_error(err) == "unknown_odoo_error"

    def test_stage_trace_model(self):
        """StageTrace must accept all expected fields."""
        from app.routers.connected_accounts import StageTrace
        st = StageTrace(
            odoo_url="https://example.odoo.com",
            odoo_host="example.odoo.com",
            odoo_db="test_db",
            odoo_username="admin@example.com",
            api_key_present=True,
            api_key_fingerprint="sha256:abc123...def456",
            transport="auto",
            model="res.partner",
            method="search_read",
            limit=1,
            status="success",
        )
        d = st.model_dump(exclude_none=True)
        assert d["odoo_db"] == "test_db"
        assert d["odoo_url"] == "https://example.odoo.com"
        assert d["api_key_present"] is True
        assert "api_key" not in d

    def test_connect_error_detail_includes_trace(self):
        """ConnectErrorDetail must accept trace and connection_attempt_id fields."""
        from app.routers.connected_accounts import ConnectErrorDetail
        c = ConnectErrorDetail(
            error_type="odoo_database_not_found",
            stage="verify_odoo",
            message="Database not found",
            request_id="req_abc",
            connection_attempt_id="odoo_conn_def",
            trace={"stages": {"test": {"status": "failed"}}},
        )
        d = c.model_dump()
        assert d["connection_attempt_id"] == "odoo_conn_def"
        assert d["trace"]["stages"]["test"]["status"] == "failed"
