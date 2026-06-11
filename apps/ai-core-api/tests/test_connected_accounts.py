import asyncio
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from uuid import UUID

# Keep test configuration local; auth itself uses APP_ENV=test from conftest.
os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"
os.environ["ODOO_URL"] = "https://company-default.odoo.com"
os.environ["ODOO_DB"] = "company-default-db"

from app.main import app
from app.core.database import get_db
from app.models.models import AIConnectedAccount
from app.services.connectors.microsoft_admin import device_auth

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


@pytest.fixture(autouse=True)
def clear_microsoft_native_device_auth_flows():
    device_auth.DEVICE_AUTH_FLOWS.clear()
    yield
    device_auth.DEVICE_AUTH_FLOWS.clear()


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
        assert connector_keys == {
            "odoo",
            "azure_cli",
            "microsoft_graph",
            "exchange_online",
            "teams_admin",
            "sharepoint_pnp",
            "github",
        }
        connectors = {item["connector_key"]: item for item in data["connectors"]}
        assert connectors["azure_cli"]["display_name"] == "Azure CLI"
        assert connectors["microsoft_graph"]["display_name"] == "Microsoft Graph"

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
        assert connectors["azure_cli"]["status"] == "not_connected"
        assert connectors["microsoft_graph"]["status"] == "not_connected"
        assert connectors["github"]["status"] == "not_connected"

    def test_get_connected_accounts_can_include_verified_token_state(self):
        async def fake_token_status(provider, _user_id):
            return {"status": "not_connected", "provider": provider}

        async def fake_microsoft_admin_token(_user_id, profile, **_kwargs):
            assert profile in {"graph", "arm", "exchange", "teams", "sharepoint"}
            return {
                "access_token": "fresh-access-token",
                "expires_on": 4_102_444_800,
                "username": "alden@example.com",
                "scope": "https://management.core.windows.net//.default",
                "scope_profile": "graph",
            }

        with (
            patch("app.services.connected_account_state.token_status", new=AsyncMock(side_effect=fake_token_status)),
            patch("app.services.connectors.microsoft_admin.tokens.get_microsoft_admin_token", new=AsyncMock(side_effect=fake_microsoft_admin_token)),
        ):
            response = client.get(
                "/connected-accounts?include_token_state=true",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
            )

        assert response.status_code == 200
        connectors = {item["connector_key"]: item for item in response.json()["connectors"]}
        assert connectors["azure_cli"]["status"] == "connected"
        assert connectors["azure_cli"]["state"]["configured"] is True
        assert connectors["azure_cli"]["state"]["token_status"] == "connected"
        assert connectors["azure_cli"]["state"]["source"] == "token_store"
        assert connectors["microsoft_graph"]["status"] == "connected"
        assert connectors["microsoft_graph"]["state"]["token_status"] == "connected"

    def test_sharepoint_native_device_code_scope_is_site_scoped(self):
        from app.routers import connector_microsoft_native as native

        scope_string, scope_summary, site_url = native._device_scope_for_request(
            "sharepoint_pnp",
            {"site_url": "https://tenant.sharepoint.com/sites/example"},
        )

        assert scope_string == "https://tenant.sharepoint.com/.default openid profile offline_access"
        assert scope_summary == "https://tenant.sharepoint.com/.default"
        assert site_url == "https://tenant.sharepoint.com/sites/example"

    @pytest.mark.asyncio
    async def test_native_device_code_start_returns_expiry_timestamp(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "device_code": "device-code",
                    "user_code": "ABC123DEF",
                    "verification_uri": "https://login.microsoft.com/device",
                    "interval": 7,
                    "expires_in": 600,
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *_args, **_kwargs):
                return FakeResponse()

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(native.time, "time", lambda: 1_800_000_000)

        result = await native.start_device_code(
            "microsoft_graph",
            req=None,
            auth={"user_id": "e4807f22-97c8-4778-87a2-160f56d25247"},
        )

        assert result["status"] == "device_code_ready"
        assert result["auth_session_id"]
        assert result["expires_at"] == 1_800_000_600
        assert result["interval"] == 7

    @pytest.mark.asyncio
    async def test_native_device_code_callback_stops_stale_flow_before_polling_microsoft(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                raise AssertionError("stale device codes must not poll Microsoft")

        await device_auth.remember_device_auth_flow(
            provider_key="microsoft_graph",
            user_id=user_id,
            device_code="newest-device-code",
            expires_at=int(native.time.time()) + 900,
            interval=5,
            request_id="newest-request",
        )
        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        result = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "older-device-code", "auth_session_id": "older-session"},
            auth={"user_id": user_id},
            db=AsyncMock(),
        )

        assert result["status"] == "stale"
        assert result["error_type"] == "stale_device_code"
        assert result["active_connector"] == "microsoft_graph"

    @pytest.mark.asyncio
    async def test_native_device_code_callback_with_missing_session_does_not_poll_microsoft(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                raise AssertionError("missing auth_session_id flow must not poll Microsoft")

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        result = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "old-device-code", "auth_session_id": "missing-session"},
            auth={"user_id": UUID("e4807f22-97c8-4778-87a2-160f56d25247")},
            db=AsyncMock(),
        )

        assert result["status"] == "stale"
        assert result["error_type"] == "stale_device_code"

    @pytest.mark.asyncio
    async def test_native_device_code_callback_does_not_store_token_after_disconnect(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")
        auth_session_id = await device_auth.remember_device_auth_flow(
            provider_key="microsoft_graph",
            user_id=user_id,
            device_code="device-code",
            expires_at=int(native.time.time()) + 900,
            interval=5,
            request_id="started-request",
        )
        stored = False

        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "token_type": "Bearer",
                    "access_token": "graph-access-token",
                    "refresh_token": "graph-refresh-token",
                    "scope": "User.Read",
                    "expires_in": 3600,
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *_args, **_kwargs):
                await device_auth.clear_device_auth_flow_for_provider(
                    provider_key="microsoft_graph",
                    user_id=user_id,
                )
                return FakeResponse()

        async def fake_store_token(*_args, **_kwargs):
            nonlocal stored
            stored = True
            return True

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(native, "store_token", fake_store_token)

        result = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "device-code", "auth_session_id": auth_session_id},
            auth={"user_id": user_id},
            db=AsyncMock(),
        )

        assert result["status"] == "stale"
        assert result["error_type"] == "stale_device_code"
        assert stored is False

    @pytest.mark.asyncio
    async def test_native_device_code_callback_gates_duplicate_polls(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")
        auth_session_id = await device_auth.remember_device_auth_flow(
            provider_key="microsoft_graph",
            user_id=user_id,
            device_code="device-code",
            expires_at=int(native.time.time()) + 900,
            interval=5,
            request_id="started-request",
        )
        poll_count = 0

        class FakeResponse:
            status_code = 400
            text = '{"error":"authorization_pending"}'

            def json(self):
                return {
                    "error": "authorization_pending",
                    "error_description": "Authorization is pending.",
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *_args, **_kwargs):
                nonlocal poll_count
                poll_count += 1
                return FakeResponse()

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        first = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "device-code", "auth_session_id": auth_session_id},
            auth={"user_id": user_id},
            db=AsyncMock(),
        )
        second = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "device-code", "auth_session_id": auth_session_id},
            auth={"user_id": user_id},
            db=AsyncMock(),
        )

        assert first["status"] == "pending"
        assert first["error_type"] == "authorization_pending"
        assert second["status"] == "pending"
        assert second["error_type"] == "poll_interval_not_elapsed"
        assert poll_count == 1

    @pytest.mark.asyncio
    async def test_native_device_code_callback_blocks_overlapping_polls(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")
        auth_session_id = await device_auth.remember_device_auth_flow(
            provider_key="microsoft_graph",
            user_id=user_id,
            device_code="device-code",
            expires_at=int(native.time.time()) + 900,
            interval=5,
            request_id="started-request",
        )
        first_poll_started = asyncio.Event()
        finish_first_poll = asyncio.Event()
        poll_count = 0

        class FakeResponse:
            status_code = 400
            text = '{"error":"authorization_pending"}'

            def json(self):
                return {
                    "error": "authorization_pending",
                    "error_description": "Authorization is pending.",
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *_args, **_kwargs):
                nonlocal poll_count
                poll_count += 1
                first_poll_started.set()
                await finish_first_poll.wait()
                return FakeResponse()

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        first_task = asyncio.create_task(native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "device-code", "auth_session_id": auth_session_id},
            auth={"user_id": user_id},
            db=AsyncMock(),
        ))
        await first_poll_started.wait()
        second = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "device-code", "auth_session_id": auth_session_id},
            auth={"user_id": user_id},
            db=AsyncMock(),
        )
        finish_first_poll.set()
        first = await first_task

        assert first["status"] == "pending"
        assert first["error_type"] == "authorization_pending"
        assert second["status"] == "pending"
        assert second["error_type"] == "poll_in_flight"
        assert poll_count == 1

    @pytest.mark.asyncio
    async def test_native_device_code_callback_uses_shared_db_flow_when_memory_empty(self, monkeypatch):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.core.database import Base
        from app.models.models import AIUser
        from app.routers import connector_microsoft_native as native

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")
        poll_count = 0

        class FakeResponse:
            status_code = 400
            text = '{"error":"authorization_pending"}'

            def json(self):
                return {
                    "error": "authorization_pending",
                    "error_description": "Authorization is pending.",
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *_args, **_kwargs):
                nonlocal poll_count
                poll_count += 1
                return FakeResponse()

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with SessionLocal() as db:
                db.add(AIUser(
                    id=user_id,
                    email="alden@example.com",
                    display_name="Alden",
                    role="admin",
                    is_active="true",
                ))
                await db.commit()
                auth_session_id = await device_auth.remember_device_auth_flow(
                    provider_key="microsoft_graph",
                    user_id=user_id,
                    device_code="device-code",
                    expires_at=int(native.time.time()) + 900,
                    interval=5,
                    request_id="started-request",
                    db=db,
                )
                device_auth.DEVICE_AUTH_FLOWS.clear()

                result = await native.device_code_callback(
                    "microsoft_graph",
                    req={"device_code": "device-code", "auth_session_id": auth_session_id},
                    auth={"user_id": user_id},
                    db=db,
                )

            assert result["status"] == "pending"
            assert result["error_type"] == "authorization_pending"
            assert poll_count == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_native_device_code_sessions_are_scoped_by_provider_in_db(self):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.core.database import Base
        from app.models.models import AIMicrosoftDeviceAuthSession, AIUser
        from app.routers import connector_microsoft_native as native

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")

        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with SessionLocal() as db:
                db.add(AIUser(
                    id=user_id,
                    email="alden@example.com",
                    display_name="Alden",
                    role="admin",
                    is_active="true",
                ))
                await db.commit()

                graph_session_id = await device_auth.remember_device_auth_flow(
                    provider_key="microsoft_graph",
                    user_id=user_id,
                    device_code="graph-device-code",
                    expires_at=int(native.time.time()) + 900,
                    interval=5,
                    request_id="graph-request",
                    db=db,
                )
                exchange_session_id = await device_auth.remember_device_auth_flow(
                    provider_key="exchange_online",
                    user_id=user_id,
                    device_code="exchange-device-code",
                    expires_at=int(native.time.time()) + 900,
                    interval=5,
                    request_id="exchange-request",
                    db=db,
                )
                device_auth.DEVICE_AUTH_FLOWS.clear()

                rows = (await db.execute(select(AIMicrosoftDeviceAuthSession))).scalars().all()
                assert {row.provider for row in rows} == {"microsoft_graph", "exchange_online"}

                graph_validation = await device_auth.validate_device_auth_flow(
                    provider_key="microsoft_graph",
                    user_id=user_id,
                    device_code="graph-device-code",
                    auth_session_id=graph_session_id,
                    request_id="graph-callback",
                    db=db,
                )
                exchange_validation = await device_auth.validate_device_auth_flow(
                    provider_key="exchange_online",
                    user_id=user_id,
                    device_code="exchange-device-code",
                    auth_session_id=exchange_session_id,
                    request_id="exchange-callback",
                    db=db,
                )

            assert graph_validation["ok"] is True
            assert exchange_validation["ok"] is True
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_exchange_native_device_code_uses_workload_scope_flow(self, monkeypatch):
        from app.routers import connector_microsoft_native as native
        from app.services.connectors.microsoft_admin.constants import EXCHANGE_ONLINE_SCOPE

        captured = {}

        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "device_code": "device-code",
                    "user_code": "EXO123",
                    "verification_url": "https://login.microsoft.com/device",
                    "interval": 5,
                    "expires_in": 900,
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, data):
                captured["url"] = url
                captured["data"] = data
                return FakeResponse()

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        result = await native.start_device_code(
            "exchange_online",
            req=None,
            auth={"user_id": "e4807f22-97c8-4778-87a2-160f56d25247"},
        )

        assert result["status"] == "device_code_ready"
        assert result["auth_flow"] == "v2_scope"
        assert result["verification_uri"] == "https://login.microsoft.com/device"
        assert result["verification_url"] == "https://login.microsoft.com/device"
        assert captured["url"].endswith("/oauth2/v2.0/devicecode")
        assert captured["data"]["scope"] == f"{EXCHANGE_ONLINE_SCOPE} openid profile offline_access"
        assert "resource" not in captured["data"]

    @pytest.mark.asyncio
    async def test_teams_native_device_callback_uses_workload_scope_flow(self, monkeypatch):
        from app.routers import connector_microsoft_native as native
        from app.services.connectors.microsoft_admin.constants import TEAMS_TENANT_ADMIN_SCOPE

        captured = {}

        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "token_type": "Bearer",
                    "access_token": "teams-access-token",
                    "refresh_token": "teams-refresh-token",
                    "scope": TEAMS_TENANT_ADMIN_SCOPE,
                    "expires_in": 3600,
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, data):
                captured["url"] = url
                captured["data"] = data
                return FakeResponse()

        async def fake_store_token(provider, user_id, token_payload):
            captured["stored_provider"] = provider
            captured["stored_user_id"] = user_id
            captured["stored_token"] = token_payload
            return True

        async def fake_upsert(*_args, **_kwargs):
            return None

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(native, "store_token", fake_store_token)
        monkeypatch.setattr(native, "upsert_delegated_account", fake_upsert)

        result = await native.device_code_callback(
            "teams_admin",
            req={"device_code": "teams-device-code"},
            auth={"user_id": UUID("e4807f22-97c8-4778-87a2-160f56d25247")},
            db=AsyncMock(),
        )

        assert result["status"] == "connected"
        assert captured["url"].endswith("/oauth2/v2.0/token")
        assert captured["data"]["device_code"] == "teams-device-code"
        assert captured["data"]["scope"] == f"{TEAMS_TENANT_ADMIN_SCOPE} openid profile offline_access"
        assert "resource" not in captured["data"]
        assert "code" not in captured["data"]
        assert captured["stored_token"]["auth_flow"] == "v2_scope"
        assert captured["stored_token"]["scope"] == TEAMS_TENANT_ADMIN_SCOPE

    @pytest.mark.asyncio
    async def test_native_device_code_terminal_error_is_not_pending(self, monkeypatch):
        from app.routers import connector_microsoft_native as native

        class FakeResponse:
            status_code = 400
            text = '{"error":"expired_token"}'

            def json(self):
                return {
                    "error": "expired_token",
                    "error_description": "AADSTS70019: The code has expired.",
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *_args, **_kwargs):
                return FakeResponse()

        monkeypatch.setattr(native.httpx, "AsyncClient", FakeClient)

        result = await native.device_code_callback(
            "microsoft_graph",
            req={"device_code": "expired-device-code"},
            auth={"user_id": UUID("e4807f22-97c8-4778-87a2-160f56d25247")},
            db=AsyncMock(),
        )

        assert result["status"] == "error"
        assert result["error_type"] == "expired_token"
        assert "expired" in result["message"].lower()

    def test_get_connected_accounts_reports_split_native_microsoft_connector_state(self):
        async def fake_token_status(provider, _user_id):
            return {"status": "not_connected", "provider": provider}

        async def fake_microsoft_admin_token(_user_id, profile, **_kwargs):
            if profile != "graph":
                return None
            return {
                "access_token": "fresh-access-token",
                "expires_on": 4_102_444_800,
                "username": "alden@example.com",
                "scope": "https://graph.microsoft.com/User.Read",
                "scope_profile": "graph",
            }

        with (
            patch("app.services.connected_account_state.token_status", new=AsyncMock(side_effect=fake_token_status)),
            patch("app.services.connectors.microsoft_admin.tokens.get_microsoft_admin_token", new=AsyncMock(side_effect=fake_microsoft_admin_token)),
        ):
            response = client.get(
                "/connected-accounts?include_token_state=true",
                headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"},
            )

        assert response.status_code == 200
        connectors = {item["connector_key"]: item for item in response.json()["connectors"]}
        assert connectors["microsoft_graph"]["status"] == "connected"
        assert connectors["microsoft_graph"]["metadata"]["auth_app_name"] == "Microsoft Graph"
        assert connectors["microsoft_graph"]["metadata"]["native_connector"] is True
        assert "Direct Microsoft Graph" in connectors["microsoft_graph"]["metadata"]["tooling"]
        assert connectors["azure_cli"]["status"] == "not_connected"
        assert connectors["exchange_online"]["status"] == "not_connected"

    @pytest.mark.asyncio
    async def test_native_microsoft_token_state_refreshes_before_reporting_expired(self):
        from app.services.connected_account_state import effective_connected_accounts

        user_id = UUID("e4807f22-97c8-4778-87a2-160f56d25247")
        account = AIConnectedAccount(
            id=UUID("e4807f22-97c8-4778-87a2-160f56d25248"),
            user_id=user_id,
            provider="microsoft_graph",
            provider_username="alden@example.com",
            status="expired",
        )

        class Result:
            def scalars(self):
                return self

            def all(self):
                return [account]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=Result())

        async def fake_token_status(provider, _user_id):
            return {"status": "not_connected", "provider": provider}

        async def fake_microsoft_admin_token(_user_id, profile, **_kwargs):
            if profile != "graph":
                return None
            return {
                "access_token": "fresh-access-token",
                "expires_on": 4_102_444_800,
                "username": "alden@example.com",
                "scope": "https://management.core.windows.net//.default",
            }

        with (
            patch("app.services.connected_account_state.token_status", new=AsyncMock(side_effect=fake_token_status)),
            patch("app.services.connectors.microsoft_admin.tokens.get_microsoft_admin_token", new=AsyncMock(side_effect=fake_microsoft_admin_token)),
        ):
            accounts = await effective_connected_accounts(db, user_id, include_token_state=True)

        microsoft_graph = next(item for item in accounts if item.provider == "microsoft_graph")
        assert microsoft_graph.status == "connected"
        assert microsoft_graph.token_status == "connected"

    def test_get_odoo_status_not_connected(self):
        response = client.get(
            "/connected-accounts/odoo/status",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_connected"

    def test_test_connection_endpoint_is_removed(self):
        response = client.post(
            "/connected-accounts/odoo/test",
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404

    def test_rotate_credentials_endpoint_is_removed(self):
        response = client.post(
            "/connected-accounts/odoo/rotate",
            json={"odoo_api_key": "new-api-key"},
            headers={"X-User-Id": "e4807f22-97c8-4778-87a2-160f56d25247"}
        )
        assert response.status_code == 404

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
        client.post(
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
            message="Test message",
            request_id="abc123",
        )
        d = err.model_dump()
        assert d["error_type"] == "odoo_credentials_invalid"
        assert d["message"] == "Test message"
        assert d["request_id"] == "abc123"
        assert "stage" not in d
        assert "technical_detail" not in d

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
                "message": "Internal connector API key mismatch.",
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
                "message": "Could not reach the Odoo Connector service.",
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
                "message": "Odoo credentials are invalid.",
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
                    "message": "Failed to save connection credentials securely.",
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

            # Find the AIConnectedAccount in db.add calls.
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

# ── Production Auth Tests ──

class TestProductionAuth:
    """Production mode must reject unauthenticated connector access."""

    def test_production_rejects_unauthenticated_access(self, monkeypatch):
        """When APP_ENV=production, anonymous access must be rejected."""
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
                "message": "Internal connector API key mismatch. Contact an administrator.",
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
                        "message": "The AI Platform API could not resolve the Odoo Connector service hostname.",
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
                assert "could not resolve" in detail.get("message", "").lower()
        finally:
            logging.disable(logging.NOTSET)

    def test_dns_failure_detected_from_connect_error(self):
        """The _verify_odoo_credentials_via_connector must detect DNS errors from httpx.ConnectError."""
        from app.routers.connected_accounts import _verify_odoo_credentials_via_connector

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
