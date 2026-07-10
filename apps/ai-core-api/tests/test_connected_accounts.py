import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

from app.core.database import get_db
from app.main import app
from app.models.models import AIConnectedAccount


USER_ID = uuid.UUID("e4807f22-97c8-4778-87a2-160f56d25247")


class FakeResult:
    def __init__(self, accounts: list[AIConnectedAccount]):
        self.accounts = accounts

    def scalar_one_or_none(self):
        return self.accounts[0] if self.accounts else None

    def scalars(self):
        return self

    def all(self):
        return self.accounts


class FakeSession:
    def __init__(self, accounts: list[AIConnectedAccount] | None = None):
        self.accounts = accounts or []
        self.added: list[AIConnectedAccount] = []
        self.committed = False

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self.accounts)

    def add(self, account: AIConnectedAccount):
        self.added.append(account)
        if account not in self.accounts:
            self.accounts.append(account)

    async def commit(self):
        self.committed = True

    async def refresh(self, _account: AIConnectedAccount):
        return None


def override_db(session: FakeSession):
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override


def clear_override():
    app.dependency_overrides.pop(get_db, None)


def account(status: str = "connected") -> AIConnectedAccount:
    now = datetime.now(timezone.utc)
    return AIConnectedAccount(
        id=uuid.uuid4(),
        user_id=USER_ID,
        provider="odoo",
        provider_username="alden@example.com",
        status=status,
        secret_reference="connected-account-secret",
        target_environment="production",
        last_verified_at=now if status == "connected" else None,
        created_at=now,
        updated_at=now,
        odoo_url="https://odoo.example.com",
        odoo_db="prod_db",
        odoo_company_id=2,
        odoo_company_name="Lots Lots More",
        odoo_currency_code="ZAR",
        odoo_currency_symbol="R",
    )


class TestConnectedAccountsFlow:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        clear_override()

    def test_get_connected_accounts_list_is_odoo_only(self):
        session = FakeSession()
        override_db(session)

        response = self.client.get(
            "/connected-accounts",
            headers={"X-User-Id": str(USER_ID)},
        )

        assert response.status_code == 200
        connectors = response.json()["connectors"]
        assert [item["connector_key"] for item in connectors] == ["odoo"]
        assert connectors[0]["display_name"] == "Odoo Enterprise"
        assert connectors[0]["status"] == "not_connected"
        assert connectors[0]["actions_available"] == ["connect"]

    def test_get_connected_accounts_reports_connected_odoo(self):
        session = FakeSession([account()])
        override_db(session)

        response = self.client.get(
            "/connected-accounts",
            headers={"X-User-Id": str(USER_ID)},
        )

        assert response.status_code == 200
        connector = response.json()["connectors"][0]
        assert connector["connector_key"] == "odoo"
        assert connector["status"] == "connected"
        assert connector["actions_available"] == ["disconnect"]
        assert connector["metadata"]["odoo_url"] == "https://odoo.example.com"
        assert connector["metadata"]["odoo_db"] == "prod_db"

    @patch("app.routers.connected_accounts._fetch_odoo_company_metadata", new=AsyncMock(return_value={
        "odoo_company_id": 2,
        "odoo_company_name": "Lots Lots More",
        "odoo_currency_code": "ZAR",
        "odoo_currency_symbol": "R",
    }))
    @patch("app.routers.connected_accounts._verify_odoo_credentials_via_connector", new=AsyncMock(return_value=None))
    @patch("app.routers.connected_accounts._store_key_vault_secret", new=AsyncMock(return_value=None))
    def test_connect_odoo_success(self):
        session = FakeSession()
        override_db(session)

        response = self.client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "my-secret-api-key",
            },
            headers={"X-User-Id": str(USER_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "odoo"
        assert data["provider_username"] == "alden@example.com"
        assert data["status"] == "connected"
        assert data["odoo_url"] == "https://odoo.example.com"
        assert data["odoo_db"] == "prod_db"
        assert "odoo_api_key" not in data
        assert "my-secret-api-key" not in str(data)
        assert session.committed is True

    @patch("app.routers.connected_accounts._store_key_vault_secret", new=AsyncMock(return_value=None))
    @patch(
        "app.routers.connected_accounts._verify_odoo_credentials_via_connector",
        new=AsyncMock(side_effect=HTTPException(
            status_code=400,
            detail={"error_type": "odoo_authentication_failed", "message": "Odoo credentials are invalid.", "request_id": "req-1"},
        )),
    )
    def test_connect_odoo_invalid_credentials(self):
        session = FakeSession()
        override_db(session)

        response = self.client.post(
            "/connected-accounts/odoo/connect",
            json={
                "odoo_url": "https://odoo.example.com",
                "odoo_db": "prod_db",
                "odoo_username": "alden@example.com",
                "odoo_api_key": "wrong-key",
            },
            headers={"X-User-Id": str(USER_ID)},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error_type"] == "odoo_authentication_failed"
        assert "credential" in detail["message"].lower()
        assert session.accounts[0].status == "error"

    def test_get_odoo_status_connected(self):
        session = FakeSession([account()])
        override_db(session)

        response = self.client.get(
            "/connected-accounts/odoo/status",
            headers={"X-User-Id": str(USER_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert data["provider_username"] == "alden@example.com"
        assert data["odoo_company_name"] == "Lots Lots More"

    @patch("app.routers.connected_accounts._delete_key_vault_secret", new=AsyncMock(return_value=None))
    def test_disconnect_odoo_clears_credentials(self):
        odoo_account = account()
        session = FakeSession([odoo_account])
        override_db(session)

        response = self.client.post(
            "/connected-accounts/odoo/disconnect",
            headers={"X-User-Id": str(USER_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "disconnected"
        assert data["provider_username"] is None
        assert data["odoo_url"] is None
        assert session.committed is True
