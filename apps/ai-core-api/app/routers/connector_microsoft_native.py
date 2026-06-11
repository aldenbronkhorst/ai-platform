"""Native Microsoft tool connectors: Azure CLI, Graph, Exchange, Teams, SharePoint."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIMicrosoftDeviceAuthSession
from app.services.connected_account_state import (
    mark_delegated_account_disconnected,
    record_delegated_diagnosis,
    sync_delegated_account_from_token,
    upsert_delegated_account,
)
from app.services.connectors.microsoft_admin.azure_cli import (
    _list_azure_subscriptions,
    ensure_azure_cli_profile,
    validate_azure_cli_profile,
)
from app.services.connectors.microsoft_admin.constants import (
    AZURE_CLI_PROVIDER,
    AZURE_TOKEN_ENDPOINT,
    AZURE_V1_DEVICE_CODE_ENDPOINT,
    AZURE_V1_TOKEN_ENDPOINT,
    AZURE_V2_DEVICE_CODE_ENDPOINT,
    EXCHANGE_ONLINE_PROVIDER,
    MICROSOFT_GRAPH_BASE_URL,
    MICROSOFT_GRAPH_PROVIDER,
    MICROSOFT_NATIVE_CONNECTOR_PROFILES,
    SHAREPOINT_PNP_PROVIDER,
    TEAMS_ADMIN_PROVIDER,
    microsoft_native_app_name_for_provider,
    microsoft_native_client_id_for_provider,
    microsoft_native_device_scope_string,
    microsoft_native_label_for_provider,
    microsoft_native_oauth_flow_for_provider,
    microsoft_native_profile_for_provider,
    microsoft_native_provider,
    microsoft_native_resource_for_provider,
    microsoft_native_scope_values,
)
from app.services.connectors.microsoft_admin.graph import _graph_error_details, _graph_response_data
from app.services.connectors.microsoft_admin.tokens import (
    extract_microsoft_admin_username,
    get_microsoft_admin_token,
    microsoft_admin_token_client_error,
    _sharepoint_scope_for_url,
)
from app.services.token_storage import delete_token, retrieve_token, store_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connector/microsoft-native", tags=["Connector"])

DEVICE_CODE_PENDING_ERRORS = {"authorization_pending", "slow_down"}
DEVICE_CODE_TERMINAL_ERRORS = {
    "authorization_declined": "Microsoft sign-in was declined.",
    "bad_verification_code": "Microsoft rejected the sign-in code. Start a new sign-in and enter the newest code.",
    "expired_token": "The Microsoft sign-in code expired before authorization completed. Start a new sign-in.",
}
DEVICE_AUTH_FLOWS: dict[str, dict[str, Any]] = {}
DEVICE_AUTH_LOCK = asyncio.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_real_db_session(db: Any) -> bool:
    return isinstance(db, AsyncSession)


def _device_auth_key(user_id: Any, provider_key: str) -> str:
    return f"{user_id}:{provider_key}"


def _device_code_hash(device_code: str) -> str:
    return hashlib.sha256(device_code.encode("utf-8")).hexdigest()


def _unix_to_utc(value: float | int) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _datetime_to_unix(value: datetime | None) -> float:
    if not value:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _db_auth_session_to_flow(session: AIMicrosoftDeviceAuthSession) -> dict[str, Any]:
    return {
        "auth_session_id": session.auth_session_id,
        "provider": session.provider,
        "device_code_hash": session.device_code_hash,
        "expires_at": int(_datetime_to_unix(session.expires_at)),
        "poll_interval": int(session.poll_interval or 5),
        "last_poll_at": _datetime_to_unix(session.last_poll_at),
        "poll_in_flight_until": _datetime_to_unix(session.poll_in_flight_until),
        "request_id": session.request_id,
    }


def _prune_device_auth_flows_locked(now: int) -> None:
    expired_keys = [
        key
        for key, flow in DEVICE_AUTH_FLOWS.items()
        if int(flow.get("expires_at") or 0) <= now
    ]
    for key in expired_keys:
        DEVICE_AUTH_FLOWS.pop(key, None)


async def _prune_device_auth_sessions(db: AsyncSession) -> None:
    await db.execute(
        delete(AIMicrosoftDeviceAuthSession)
        .where(AIMicrosoftDeviceAuthSession.expires_at <= _utcnow())
        .execution_options(synchronize_session=False)
    )


async def _load_device_auth_flow(
    *,
    db: Any,
    provider_key: str,
    user_id: Any,
    for_update: bool = False,
) -> tuple[dict[str, Any] | None, AIMicrosoftDeviceAuthSession | None]:
    if _is_real_db_session(db):
        await _prune_device_auth_sessions(db)
        stmt = select(AIMicrosoftDeviceAuthSession).where(
            AIMicrosoftDeviceAuthSession.user_id == user_id,
            AIMicrosoftDeviceAuthSession.provider == provider_key,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        return (_db_auth_session_to_flow(session), session) if session else (None, None)

    now = int(time.time())
    async with DEVICE_AUTH_LOCK:
        _prune_device_auth_flows_locked(now)
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
    return (dict(flow), None) if flow else (None, None)


async def _remember_device_auth_flow(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    expires_at: int,
    interval: int,
    request_id: str,
    db: Any = None,
) -> str:
    """Record the newest Microsoft device-code flow for a user and connector.

    Device-code polling is tracked per native Microsoft tool so Azure, Graph,
    Exchange, Teams, and SharePoint sign-ins cannot overwrite each other.
    Within one connector, the newest sign-in replaces older codes so stale
    browser tabs stop polling before they hit Microsoft's token endpoint.
    """
    auth_session_id = uuid.uuid4().hex
    async with DEVICE_AUTH_LOCK:
        _prune_device_auth_flows_locked(int(time.time()))
        DEVICE_AUTH_FLOWS[_device_auth_key(user_id, provider_key)] = {
            "auth_session_id": auth_session_id,
            "provider": provider_key,
            "device_code_hash": _device_code_hash(device_code),
            "expires_at": int(expires_at),
            "poll_interval": max(1, int(interval or 5)),
            "last_poll_at": 0.0,
            "poll_in_flight_until": 0.0,
            "request_id": request_id,
        }
    if _is_real_db_session(db):
        await _prune_device_auth_sessions(db)
        result = await db.execute(
            select(AIMicrosoftDeviceAuthSession)
            .where(
                AIMicrosoftDeviceAuthSession.user_id == user_id,
                AIMicrosoftDeviceAuthSession.provider == provider_key,
            )
            .with_for_update()
        )
        session = result.scalar_one_or_none()
        if not session:
            session = AIMicrosoftDeviceAuthSession(user_id=user_id)
            db.add(session)
        session.auth_session_id = auth_session_id
        session.provider = provider_key
        session.device_code_hash = _device_code_hash(device_code)
        session.expires_at = _unix_to_utc(expires_at)
        session.poll_interval = max(1, int(interval or 5))
        session.last_poll_at = None
        session.poll_in_flight_until = None
        session.request_id = request_id
        session.updated_at = _utcnow()
        await db.commit()
    return auth_session_id


async def _validate_device_auth_flow(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    request_id: str,
    db: Any = None,
) -> dict[str, Any]:
    """Return a stale/expired response when a callback is no longer current."""
    flow, _ = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id)

    if not flow:
        if auth_session_id:
            logger.info(
                "Native Microsoft device-code callback stopped for missing session provider=%s request_id=%s auth_session_id=%s",
                provider_key,
                request_id,
                auth_session_id,
            )
            return {
                "ok": False,
                "response": {
                    "status": "stale",
                    "connector": provider_key,
                    "error": "stale_device_code",
                    "error_type": "stale_device_code",
                    "message": "This Microsoft sign-in session is no longer active. Start a fresh sign-in and use the newest code.",
                    "request_id": request_id,
                },
            }
        return {"ok": True, "auth_session_id": auth_session_id}

    active_session_id = str(flow.get("auth_session_id") or "")
    active_provider = str(flow.get("provider") or "")
    active_hash = str(flow.get("device_code_hash") or "")
    supplied_session_id = str(auth_session_id or "").strip()
    same_session = (
        active_provider == provider_key
        and active_hash == _device_code_hash(device_code)
        and (not supplied_session_id or supplied_session_id == active_session_id)
    )
    if same_session:
        return {"ok": True, "auth_session_id": active_session_id}

    logger.info(
        "Native Microsoft stale device-code callback stopped provider=%s active_provider=%s request_id=%s active_request_id=%s",
        provider_key,
        active_provider,
        request_id,
        flow.get("request_id"),
    )
    return {
        "ok": False,
        "response": {
            "status": "stale",
            "connector": provider_key,
            "error": "stale_device_code",
            "error_type": "stale_device_code",
            "message": (
                "A newer Microsoft sign-in was started. This older device code has been stopped; "
                "use the newest code shown in the connector panel."
            ),
            "request_id": request_id,
            "active_auth_session_id": active_session_id,
            "active_connector": active_provider,
        },
    }


async def _claim_device_auth_poll(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    request_id: str,
    db: Any = None,
) -> dict[str, Any]:
    """Allow only one Microsoft token poll per active flow interval.

    Device-code flows are sensitive to over-polling. Browser refreshes, stale
    React timers, or multiple app tabs can otherwise make several token
    requests for the same user code and trigger Microsoft throttling that looks
    like the external device-login page is looping.
    """
    validation = await _validate_device_auth_flow(
        provider_key=provider_key,
        user_id=user_id,
        device_code=device_code,
        auth_session_id=auth_session_id,
        request_id=request_id,
        db=db,
    )
    if not validation.get("ok"):
        return validation

    now = time.time()
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if not flow or not session:
            return validation
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") != provider_key
            or str(flow.get("device_code_hash") or "") != _device_code_hash(device_code)
            or (supplied_session_id and supplied_session_id != str(flow.get("auth_session_id") or ""))
        ):
            return {
                "ok": False,
                "response": {
                    "status": "stale",
                    "connector": provider_key,
                    "error": "stale_device_code",
                    "error_type": "stale_device_code",
                    "message": (
                        "A newer Microsoft sign-in was started. This older device code has been stopped; "
                        "use the newest code shown in the connector panel."
                    ),
                    "request_id": request_id,
                    "active_auth_session_id": str(flow.get("auth_session_id") or ""),
                    "active_connector": str(flow.get("provider") or ""),
                },
            }
        interval = max(1, int(flow.get("poll_interval") or 5))
        in_flight_until = float(flow.get("poll_in_flight_until") or 0.0)
        if in_flight_until > now:
            retry_after = max(1, int(min(interval, in_flight_until - now) + 0.999))
            return {
                "ok": False,
                "response": {
                    "status": "pending",
                    "connector": provider_key,
                    "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or ""),
                    "error": "poll_in_flight",
                    "error_type": "poll_in_flight",
                    "message": "Waiting for Microsoft to complete sign-in.",
                    "interval": retry_after,
                    "request_id": request_id,
                },
            }
        last_poll_at = float(flow.get("last_poll_at") or 0.0)
        elapsed = now - last_poll_at
        if last_poll_at and elapsed < interval:
            retry_after = max(1, int(interval - elapsed + 0.999))
            return {
                "ok": False,
                "response": {
                    "status": "pending",
                    "connector": provider_key,
                    "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or ""),
                    "error": "poll_interval_not_elapsed",
                    "error_type": "poll_interval_not_elapsed",
                    "message": "Waiting for Microsoft to complete sign-in.",
                    "interval": retry_after,
                    "request_id": request_id,
                },
            }
        session.last_poll_at = _unix_to_utc(now)
        session.poll_in_flight_until = _unix_to_utc(now + 35.0)
        session.updated_at = _utcnow()
        await db.commit()
        return {"ok": True, "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or "")}

    async with DEVICE_AUTH_LOCK:
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
        if not flow:
            return validation
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") != provider_key
            or str(flow.get("device_code_hash") or "") != _device_code_hash(device_code)
            or (supplied_session_id and supplied_session_id != str(flow.get("auth_session_id") or ""))
        ):
            return {
                "ok": False,
                "response": {
                    "status": "stale",
                    "connector": provider_key,
                    "error": "stale_device_code",
                    "error_type": "stale_device_code",
                    "message": (
                        "A newer Microsoft sign-in was started. This older device code has been stopped; "
                        "use the newest code shown in the connector panel."
                    ),
                    "request_id": request_id,
                    "active_auth_session_id": str(flow.get("auth_session_id") or ""),
                    "active_connector": str(flow.get("provider") or ""),
                },
            }
        interval = max(1, int(flow.get("poll_interval") or 5))
        in_flight_until = float(flow.get("poll_in_flight_until") or 0.0)
        if in_flight_until > now:
            retry_after = max(1, int(min(interval, in_flight_until - now) + 0.999))
            return {
                "ok": False,
                "response": {
                    "status": "pending",
                    "connector": provider_key,
                    "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or ""),
                    "error": "poll_in_flight",
                    "error_type": "poll_in_flight",
                    "message": "Waiting for Microsoft to complete sign-in.",
                    "interval": retry_after,
                    "request_id": request_id,
                },
            }
        last_poll_at = float(flow.get("last_poll_at") or 0.0)
        elapsed = now - last_poll_at
        if last_poll_at and elapsed < interval:
            retry_after = max(1, int(interval - elapsed + 0.999))
            return {
                "ok": False,
                "response": {
                    "status": "pending",
                    "connector": provider_key,
                    "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or ""),
                    "error": "poll_interval_not_elapsed",
                    "error_type": "poll_interval_not_elapsed",
                    "message": "Waiting for Microsoft to complete sign-in.",
                    "interval": retry_after,
                    "request_id": request_id,
                },
            }
        flow["last_poll_at"] = now
        flow["poll_in_flight_until"] = now + 35.0
        return {"ok": True, "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or "")}


async def _release_device_auth_poll(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    db: Any = None,
) -> None:
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if not flow or not session:
            return
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") == provider_key
            and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
            and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
        ):
            session.poll_in_flight_until = None
            session.updated_at = _utcnow()
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
        if not flow:
            return
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") == provider_key
            and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
            and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
        ):
            flow["poll_in_flight_until"] = 0.0


async def _update_device_auth_poll_interval(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    interval: int,
    db: Any = None,
) -> None:
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if not flow or not session:
            return
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") == provider_key
            and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
            and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
        ):
            session.poll_interval = max(1, int(interval or flow.get("poll_interval") or 5))
            session.updated_at = _utcnow()
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
        if not flow:
            return
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") == provider_key
            and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
            and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
        ):
            flow["poll_interval"] = max(1, int(interval or flow.get("poll_interval") or 5))


async def _is_device_auth_flow_current(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    db: Any = None,
) -> bool:
    flow, _ = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id)
    if not flow:
        return False
    supplied_session_id = str(auth_session_id or "").strip()
    return (
        str(flow.get("provider") or "") == provider_key
        and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
        and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
    )


async def _clear_device_auth_flow(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    db: Any = None,
) -> None:
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if not flow or not session:
            return
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") == provider_key
            and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
            and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
        ):
            await db.delete(session)
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        key = _device_auth_key(user_id, provider_key)
        flow = DEVICE_AUTH_FLOWS.get(key)
        if not flow:
            return
        supplied_session_id = str(auth_session_id or "").strip()
        if (
            str(flow.get("provider") or "") == provider_key
            and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
            and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
        ):
            DEVICE_AUTH_FLOWS.pop(key, None)


async def _clear_device_auth_flow_for_provider(*, provider_key: str, user_id: Any, db: Any = None) -> None:
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if flow and session and str(flow.get("provider") or "") == provider_key:
            await db.delete(session)
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        key = _device_auth_key(user_id, provider_key)
        flow = DEVICE_AUTH_FLOWS.get(key)
        if flow and str(flow.get("provider") or "") == provider_key:
            DEVICE_AUTH_FLOWS.pop(key, None)


def _provider_or_404(provider: str) -> str:
    normalized = microsoft_native_provider(provider)
    if not normalized:
        raise HTTPException(status_code=404, detail="Unknown Microsoft connector")
    return normalized


def _connect_unsupported(provider: str) -> dict[str, Any] | None:
    client_id = microsoft_native_client_id_for_provider(provider)
    scopes = microsoft_native_scope_values(provider)
    if not client_id:
        return {
            "status": "error",
            "error": "native_client_not_configured",
            "message": (
                f"{microsoft_native_label_for_provider(provider)} does not expose a configured native public client "
                "in this environment. This connector must use its module-native login path, not the old all-in-one app."
            ),
        }
    if provider != SHAREPOINT_PNP_PROVIDER and not scopes:
        return {
            "status": "error",
            "error": "scopes_not_configured",
            "message": f"{microsoft_native_label_for_provider(provider)} has no delegated scopes configured.",
        }
    return None


def _request_site_url(req: dict[str, Any] | None) -> str:
    return str((req or {}).get("site_url") or (req or {}).get("admin_url") or "").strip()


def _device_scope_for_request(provider: str, req: dict[str, Any] | None) -> tuple[str, str, str | None]:
    if provider != SHAREPOINT_PNP_PROVIDER:
        return (
            microsoft_native_device_scope_string(provider),
            ", ".join(microsoft_native_scope_values(provider)),
            None,
        )
    site_url = _request_site_url(req)
    scope = _sharepoint_scope_for_url(site_url)
    if not scope:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error": "site_url_required",
                "message": "SharePoint/PnP sign-in requires an HTTPS SharePoint site URL.",
            },
        )
    return f"{scope} openid profile offline_access", scope, site_url


def _device_auth_for_request(provider: str, req: dict[str, Any] | None) -> tuple[str, str, str, str | None]:
    """Return auth flow, OAuth value, human summary, and optional SharePoint site URL."""
    if provider == SHAREPOINT_PNP_PROVIDER:
        scope, summary, site_url = _device_scope_for_request(provider, req)
        return "v2_scope", scope, summary, site_url

    flow = microsoft_native_oauth_flow_for_provider(provider)
    if flow == "v1_resource":
        resource = microsoft_native_resource_for_provider(provider)
        if not resource:
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "error",
                    "error": "resource_not_configured",
                    "message": f"{microsoft_native_label_for_provider(provider)} has no OAuth resource configured.",
                },
            )
        return flow, resource, resource, None

    scope, summary, site_url = _device_scope_for_request(provider, req)
    return "v2_scope", scope, summary, site_url


@router.post("/{provider}/device-code")
async def start_device_code(
    provider: str,
    req: dict[str, Any] | None = Body(default=None),
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Start a device-code flow for one native Microsoft tool connector."""
    user_id = auth.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing auth")
    provider_key = _provider_or_404(provider)
    unsupported = _connect_unsupported(provider_key)
    request_id = uuid.uuid4().hex[:16]
    if unsupported:
        return {**unsupported, "connector": provider_key, "request_id": request_id}

    client_id = microsoft_native_client_id_for_provider(provider_key)
    auth_flow, oauth_value, scope_summary, site_url = _device_auth_for_request(provider_key, req)
    endpoint = AZURE_V1_DEVICE_CODE_ENDPOINT if auth_flow == "v1_resource" else AZURE_V2_DEVICE_CODE_ENDPOINT
    payload = {"client_id": client_id, "resource": oauth_value} if auth_flow == "v1_resource" else {"client_id": client_id, "scope": oauth_value}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                endpoint,
                data=payload,
            )
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            logger.warning(
                "Native Microsoft device-code start rejected provider=%s request_id=%s status=%s error=%s description=%s",
                provider_key,
                request_id,
                resp.status_code,
                data.get("error"),
                data.get("error_description"),
            )
            return {
                "status": "error",
                "connector": provider_key,
                "error": data.get("error_description") or data.get("error") or resp.text[:500],
                "error_type": data.get("error") or "device_code_start_failed",
                "request_id": request_id,
            }
        logger.info(
            "Native Microsoft device-code ready provider=%s request_id=%s app=%s expires_in=%s interval=%s",
            provider_key,
            request_id,
            microsoft_native_app_name_for_provider(provider_key),
            data.get("expires_in", 900),
            data.get("interval", 5),
        )
        expires_at = int(time.time()) + int(data.get("expires_in") or 900)
        auth_session_id = await _remember_device_auth_flow(
            provider_key=provider_key,
            user_id=user_id,
            device_code=data["device_code"],
            expires_at=expires_at,
            interval=int(data.get("interval") or 5),
            request_id=request_id,
            db=db,
        )
        verification_url = data.get("verification_uri") or data.get("verification_url") or "https://microsoft.com/devicelogin"
        return {
            "status": "device_code_ready",
            "connector": provider_key,
            "auth_session_id": auth_session_id,
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": verification_url,
            "verification_url": verification_url,
            "interval": data.get("interval", 5),
            "expires_in": data.get("expires_in", 900),
            "expires_at": expires_at,
            "scope_profile": microsoft_native_profile_for_provider(provider_key),
            "scope_label": microsoft_native_label_for_provider(provider_key),
            "scope_summary": scope_summary,
            "site_url": site_url,
            "client_id": client_id,
            "auth_app_name": microsoft_native_app_name_for_provider(provider_key),
            "auth_flow": auth_flow,
            "request_id": request_id,
        }
    except Exception as exc:
        logger.warning("Native Microsoft device-code start failed provider=%s request_id=%s: %s", provider_key, request_id, exc)
        return {
            "status": "error",
            "connector": provider_key,
            "error": "device_code_start_failed",
            "message": "Could not start Microsoft device authentication. Check connector logs with this request_id.",
            "request_id": request_id,
        }


@router.post("/{provider}/token-callback")
async def device_code_callback(
    provider: str,
    req: dict,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Poll one native Microsoft device code and store that connector token."""
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    device_code = req.get("device_code", "")
    auth_session_id = str(req.get("auth_session_id") or "").strip() or None
    if not device_code or not user_id:
        raise HTTPException(status_code=400, detail="Missing device_code or auth")

    unsupported = _connect_unsupported(provider_key)
    request_id = uuid.uuid4().hex[:16]
    if unsupported:
        return {**unsupported, "connector": provider_key, "request_id": request_id}

    client_id = microsoft_native_client_id_for_provider(provider_key)
    auth_flow, oauth_value, scope_summary, site_url = _device_auth_for_request(provider_key, req)
    flow_state = await _claim_device_auth_poll(
        provider_key=provider_key,
        user_id=user_id,
        device_code=device_code,
        auth_session_id=auth_session_id,
        request_id=request_id,
        db=db,
    )
    if not flow_state.get("ok"):
        return flow_state["response"]
    auth_session_id = flow_state.get("auth_session_id") or auth_session_id
    claimed_poll = True
    if auth_flow == "v1_resource":
        endpoint = AZURE_V1_TOKEN_ENDPOINT
        token_payload_request = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "code": device_code,
            "resource": oauth_value,
        }
    else:
        endpoint = AZURE_TOKEN_ENDPOINT
        token_payload_request = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": device_code,
            "scope": oauth_value,
            "client_info": "1",
        }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                endpoint,
                data=token_payload_request,
            )
        data = resp.json()
        if "error" in data:
            error_code = str(data.get("error") or "token_exchange_failed")
            is_pending = error_code in DEVICE_CODE_PENDING_ERRORS
            if not is_pending:
                logger.warning(
                    "Native Microsoft device-code token exchange rejected provider=%s request_id=%s status=%s error=%s description=%s",
                    provider_key,
                    request_id,
                    resp.status_code,
                    data.get("error"),
                    data.get("error_description"),
                )
            message = DEVICE_CODE_TERMINAL_ERRORS.get(error_code) or data.get("error_description") or error_code
            if not is_pending:
                await _clear_device_auth_flow(
                    provider_key=provider_key,
                    user_id=user_id,
                    device_code=device_code,
                    auth_session_id=auth_session_id,
                    db=db,
                )
            elif error_code == "slow_down":
                await _update_device_auth_poll_interval(
                    provider_key=provider_key,
                    user_id=user_id,
                    device_code=device_code,
                    auth_session_id=auth_session_id,
                    interval=10,
                    db=db,
                )
            return {
                "status": "pending" if is_pending else "error",
                "connector": provider_key,
                "auth_session_id": auth_session_id,
                "error": data.get("error_description", error_code),
                "error_type": error_code,
                "message": message,
                "interval": 10 if error_code == "slow_down" else None,
                "request_id": request_id,
            }

        if auth_session_id and not await _is_device_auth_flow_current(
            provider_key=provider_key,
            user_id=user_id,
            device_code=device_code,
            auth_session_id=auth_session_id,
            db=db,
        ):
            logger.info(
                "Native Microsoft token exchange succeeded after session was cancelled provider=%s request_id=%s",
                provider_key,
                request_id,
            )
            return {
                "status": "stale",
                "connector": provider_key,
                "auth_session_id": auth_session_id,
                "error": "stale_device_code",
                "error_type": "stale_device_code",
                "message": "This Microsoft sign-in was cancelled before it completed. Start a fresh sign-in and use the newest code.",
                "request_id": request_id,
            }

        token_payload = {
            "provider": provider_key,
            "client_id": client_id,
            "auth_flow": auth_flow,
            "token_type": data.get("token_type"),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "scope": data.get("scope") or scope_summary,
            "resource": data.get("resource") or (oauth_value if auth_flow == "v1_resource" else None),
            "scope_profile": microsoft_native_profile_for_provider(provider_key),
            "id_token": data.get("id_token"),
            "client_info": data.get("client_info"),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        if site_url:
            token_payload["site_url"] = site_url
        token_payload["username"] = extract_microsoft_admin_username(token_payload)
        stored = await store_token(provider_key, user_id, token_payload)
        if not stored:
            return {
                "status": "error",
                "connector": provider_key,
                "error": "key_vault_write_failed",
                "message": "Could not store credentials securely.",
                "request_id": request_id,
            }
        await upsert_delegated_account(
            db,
            provider_key,
            user_id,
            token_data=token_payload,
            status="connected",
            username=token_payload.get("username"),
            permission_summary=f"{microsoft_native_label_for_provider(provider_key)} connected with its own native Microsoft sign-in.",
            commit=True,
        )
        await _clear_device_auth_flow(
            provider_key=provider_key,
            user_id=user_id,
            device_code=device_code,
            auth_session_id=auth_session_id,
            db=db,
        )
        return {
            "status": "connected",
            "connector": provider_key,
            "auth_session_id": auth_session_id,
            "request_id": request_id,
            "scope_profile": token_payload["scope_profile"],
            "scope_label": microsoft_native_label_for_provider(provider_key),
            "scope_summary": scope_summary,
            "site_url": site_url,
            "auth_app_name": microsoft_native_app_name_for_provider(provider_key),
            "message": f"{microsoft_native_label_for_provider(provider_key)} connected.",
        }
    except Exception as exc:
        logger.warning("Native Microsoft device-code callback failed provider=%s request_id=%s: %s", provider_key, request_id, exc)
        return {
            "status": "error",
            "connector": provider_key,
            "error": "device_code_callback_failed",
            "message": "Could not complete Microsoft device authentication. Check connector logs with this request_id.",
            "request_id": request_id,
        }
    finally:
        if claimed_poll:
            await _release_device_auth_poll(
                provider_key=provider_key,
                user_id=user_id,
                device_code=device_code,
                auth_session_id=auth_session_id,
                db=db,
            )


@router.get("/{provider}/status")
async def microsoft_native_status(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    return await sync_delegated_account_from_token(db, provider_key, user_id, commit=True) if user_id else {"status": "not_connected"}


@router.post("/{provider}/diagnose")
async def microsoft_native_diagnose(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    result = await diagnose_microsoft_native_connection(provider_key, user_id)
    if user_id:
        await record_delegated_diagnosis(db, provider_key, user_id, result, commit=True)
    return result


@router.post("/{provider}/validate")
async def microsoft_native_validate(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    return await microsoft_native_diagnose(provider=provider, auth=auth, db=db)


@router.post("/{provider}/disconnect")
async def microsoft_native_disconnect(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    if user_id:
        await _clear_device_auth_flow_for_provider(provider_key=provider_key, user_id=user_id, db=db)
        await delete_token(provider_key, user_id)
        await mark_delegated_account_disconnected(db, provider_key, user_id, commit=True)
    return {"status": "disconnected", "connector": provider_key}


async def diagnose_microsoft_native_connection(provider: str, user_id: Any) -> dict[str, Any]:
    provider_key = _provider_or_404(provider)
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token(provider_key, user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": provider_key,
            "request_id": request_id,
            "message": f"{microsoft_native_label_for_provider(provider_key)} is not connected for this user.",
        }
    client_error = microsoft_admin_token_client_error({**token_data, "provider": provider_key})
    if client_error:
        return {
            "status": "failed",
            "connector": provider_key,
            "request_id": request_id,
            "error_type": "wrong_native_client",
            "message": client_error,
        }

    if provider_key == AZURE_CLI_PROVIDER:
        return await _diagnose_azure_cli(user_id, request_id)
    if provider_key == MICROSOFT_GRAPH_PROVIDER:
        return await _diagnose_graph(user_id, request_id)
    if provider_key in {EXCHANGE_ONLINE_PROVIDER, TEAMS_ADMIN_PROVIDER, SHAREPOINT_PNP_PROVIDER}:
        return await _diagnose_workload_token(provider_key, user_id, request_id, token_data)

    return {
        "status": "success",
        "connector": provider_key,
        "request_id": request_id,
        "message": (
            f"{microsoft_native_label_for_provider(provider_key)} has its own token. "
            "Actual command access is still controlled by the signed-in user's Microsoft roles and workload permissions."
        ),
        "provider_username": token_data.get("username"),
    }


async def _diagnose_workload_token(provider_key: str, user_id: Any, request_id: str, token_data: dict[str, Any]) -> dict[str, Any]:
    profile = microsoft_native_profile_for_provider(provider_key)
    context: dict[str, Any] = {}
    if provider_key == SHAREPOINT_PNP_PROVIDER:
        site_url = str(token_data.get("site_url") or "").strip()
        if not site_url:
            return {
                "status": "failed",
                "connector": provider_key,
                "request_id": request_id,
                "message": "SharePoint/PnP is connected without a site URL. Reconnect it with a SharePoint site/admin URL.",
                "error_type": "site_url_required",
            }
        context["site_url"] = site_url
    token = await get_microsoft_admin_token(user_id, profile, **context)
    if not token or not token.get("access_token") or token.get("refresh_error"):
        return {
            "status": "failed",
            "connector": provider_key,
            "request_id": request_id,
            "message": token.get("refresh_error") if isinstance(token, dict) else f"{microsoft_native_label_for_provider(provider_key)} token is not available.",
            "error_type": token.get("error_type") if isinstance(token, dict) else "not_connected",
        }
    return {
        "status": "success",
        "connector": provider_key,
        "request_id": request_id,
        "message": (
            f"{microsoft_native_label_for_provider(provider_key)} token refreshed successfully. "
            "Actual command access is still controlled by the signed-in user's Microsoft roles and workload permissions."
        ),
        "provider_username": token.get("username") or token_data.get("username"),
    }


async def _diagnose_azure_cli(user_id: Any, request_id: str) -> dict[str, Any]:
    token = await get_microsoft_admin_token(user_id, "arm")
    if not token or not token.get("access_token") or token.get("refresh_error"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": token.get("refresh_error") if isinstance(token, dict) else "Azure CLI token is not available.",
            "error_type": token.get("error_type") if isinstance(token, dict) else "not_connected",
        }
    subscriptions_result = await _list_azure_subscriptions(token["access_token"])
    if not subscriptions_result.get("ok"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": subscriptions_result.get("message", "Azure subscription discovery failed."),
            "stderr": subscriptions_result.get("stderr", ""),
        }
    profile = await ensure_azure_cli_profile(user_id, token, subscriptions_result=subscriptions_result)
    if not profile.get("ready"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": profile.get("message") or "Azure CLI profile could not be prepared.",
        }
    validation = await validate_azure_cli_profile(user_id)
    if not validation.get("ready"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": validation.get("message") or "Azure CLI profile validation failed.",
            "stderr": validation.get("stderr", ""),
        }
    subscriptions = subscriptions_result.get("subscriptions", [])
    return {
        "status": "success",
        "connector": AZURE_CLI_PROVIDER,
        "request_id": request_id,
        "message": "Azure CLI is connected and the native az profile is ready.",
        "subscriptions_count": len(subscriptions),
        "subscriptions": [
            {
                "subscription_id": sub.get("subscriptionId"),
                "display_name": sub.get("displayName"),
                "state": sub.get("state"),
            }
            for sub in subscriptions[:10]
        ],
    }


async def _diagnose_graph(user_id: Any, request_id: str) -> dict[str, Any]:
    token = await get_microsoft_admin_token(user_id, "graph")
    if not token or not token.get("access_token") or token.get("refresh_error"):
        return {
            "status": "failed",
            "connector": MICROSOFT_GRAPH_PROVIDER,
            "request_id": request_id,
            "message": token.get("refresh_error") if isinstance(token, dict) else "Microsoft Graph token is not available.",
            "error_type": token.get("error_type") if isinstance(token, dict) else "not_connected",
        }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/v1.0/me?$select=id,displayName,userPrincipalName,mail",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
    graph_data = _graph_response_data(response)
    error_type, graph_message = _graph_error_details(graph_data, response.status_code)
    if response.status_code >= 400:
        return {
            "status": "failed",
            "connector": MICROSOFT_GRAPH_PROVIDER,
            "request_id": request_id,
            "message": graph_message or "Microsoft Graph validation failed.",
            "error_type": error_type or "graph_validation_failed",
            "status_code": response.status_code,
        }
    return {
        "status": "success",
        "connector": MICROSOFT_GRAPH_PROVIDER,
        "request_id": request_id,
        "message": "Microsoft Graph is connected and /me validation succeeded.",
        "graph_user": graph_data if isinstance(graph_data, dict) else {},
    }
