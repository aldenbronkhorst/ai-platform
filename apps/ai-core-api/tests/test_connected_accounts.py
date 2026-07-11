import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["CONNECTOR_ENDPOINTS_JSON"] = '{"erp":{"base_url":"http://connector:8000"}}'
os.environ["CONNECTOR_INTERNAL_API_KEY"] = "test-key"

from app.core.database import get_db
from app.main import app
from app.models.models import AIConnectedAccount
from app.services.external_connectors import ConnectorRequestError


USER_ID = uuid.UUID("e4807f22-97c8-4778-87a2-160f56d25247")
MANIFEST = {
    "id": "erp",
    "display_name": "Business ERP",
    "subtitle": "External ERP connector",
    "broker_target": "erp",
    "auth_method": "api_key",
    "connection_fields": [
        {"name": "url", "label": "URL", "required": True, "secret": False},
        {"name": "username", "label": "Username", "required": True, "secret": False},
        {"name": "api_key", "label": "API key", "required": True, "secret": True},
    ],
}


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
        self.committed = False

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self.accounts)

    def add(self, account: AIConnectedAccount):
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


def account(status: str = "connected") -> AIConnectedAccount:
    now = datetime.now(timezone.utc)
    return AIConnectedAccount(
        id=uuid.uuid4(),
        user_id=USER_ID,
        provider="erp",
        provider_user_id="42",
        provider_username="person@example.com",
        status=status,
        secret_reference="connected-account-secret",
        target_environment="production",
        last_verified_at=now if status == "connected" else None,
        created_at=now,
        updated_at=now,
        configuration_json={"url": "https://erp.example.com", "username": "person@example.com"},
        connector_metadata_json={"company_name": "Example Company"},
    )


class TestConnectedAccountsFlow:
    def setup_method(self):
        self.client = TestClient(app)
        self.patches = [
            patch("app.routers.connected_accounts.configured_connector_ids", return_value=("erp",)),
            patch("app.routers.connected_accounts.connector_endpoint", return_value=object()),
            patch("app.routers.connected_accounts.load_connector_manifest", new=AsyncMock(return_value=MANIFEST)),
        ]
        for item in self.patches:
            item.start()

    def teardown_method(self):
        app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches):
            item.stop()

    def test_list_is_driven_by_registered_manifests(self):
        override_db(FakeSession())
        response = self.client.get("/connected-accounts", headers={"X-User-Id": str(USER_ID)})
        assert response.status_code == 200
        connector = response.json()["connectors"][0]
        assert connector["connector_key"] == "erp"
        assert connector["display_name"] == "Business ERP"
        assert connector["status"] == "not_connected"
        assert connector["manifest"]["connection_fields"][2]["secret"] is True

    def test_list_never_returns_saved_secrets(self):
        override_db(FakeSession([account()]))
        response = self.client.get("/connected-accounts", headers={"X-User-Id": str(USER_ID)})
        connector = response.json()["connectors"][0]
        assert connector["status"] == "connected"
        assert connector["configuration"] == {
            "url": "https://erp.example.com",
            "username": "person@example.com",
        }
        assert "connected-account-secret" not in str(connector)
        assert "api_key" not in connector["configuration"]

    @patch("app.routers.connected_accounts._store_secret", new=AsyncMock(return_value=None))
    @patch("app.routers.connected_accounts.verify_connector_values", new=AsyncMock(return_value={
        "status": "connected",
        "identity": {"id": "42", "username": "person@example.com"},
        "configuration": {"url": "https://erp.example.com", "username": "person@example.com", "api_key": "must-not-leak"},
        "metadata": {"company_name": "Example Company"},
    }))
    def test_connect_verifies_then_stores_generic_fields(self):
        session = FakeSession()
        override_db(session)
        response = self.client.post(
            "/connected-accounts/erp/connect",
            json={"values": {"url": "erp.example.com", "username": "person@example.com", "api_key": "my-secret"}},
            headers={"X-User-Id": str(USER_ID)},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert data["configuration"] == {"url": "https://erp.example.com", "username": "person@example.com"}
        assert "my-secret" not in str(data)
        assert "must-not-leak" not in str(data)
        assert session.accounts[0].connector_metadata_json == {"company_name": "Example Company"}
        assert session.committed is True

    @patch("app.routers.connected_accounts.verify_connector_values", new=AsyncMock(side_effect=ConnectorRequestError(
        400, {"error_type": "authentication_failed", "message": "Credentials are invalid."}
    )))
    def test_invalid_credentials_are_not_persisted(self):
        session = FakeSession()
        override_db(session)
        response = self.client.post(
            "/connected-accounts/erp/connect",
            json={"values": {"url": "https://erp.example.com", "username": "person@example.com", "api_key": "wrong"}},
            headers={"X-User-Id": str(USER_ID)},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error_type"] == "authentication_failed"
        assert session.accounts == []

    @patch("app.routers.connected_accounts._delete_secret", new=AsyncMock(return_value=None))
    def test_disconnect_removes_credentials_and_configuration(self):
        linked = account()
        session = FakeSession([linked])
        override_db(session)
        response = self.client.delete("/connected-accounts/erp", headers={"X-User-Id": str(USER_ID)})
        assert response.status_code == 200
        assert response.json()["status"] == "disconnected"
        assert linked.secret_reference is None
        assert linked.configuration_json is None
        assert linked.connector_metadata_json is None
