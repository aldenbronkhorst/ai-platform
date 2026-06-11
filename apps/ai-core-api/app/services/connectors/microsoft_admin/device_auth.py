from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIMicrosoftDeviceAuthSession

logger = logging.getLogger(__name__)

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


async def remember_device_auth_flow(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    expires_at: int,
    interval: int,
    request_id: str,
    db: Any = None,
) -> str:
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


async def validate_device_auth_flow(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    request_id: str,
    db: Any = None,
) -> dict[str, Any]:
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


def _stale_device_code_response(provider_key: str, request_id: str, flow: dict[str, Any]) -> dict[str, Any]:
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


def _poll_wait_response(
    *,
    provider_key: str,
    auth_session_id: str | None,
    flow: dict[str, Any],
    request_id: str,
    error_type: str,
    retry_after: int,
) -> dict[str, Any]:
    return {
        "ok": False,
        "response": {
            "status": "pending",
            "connector": provider_key,
            "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or ""),
            "error": error_type,
            "error_type": error_type,
            "message": "Waiting for Microsoft to complete sign-in.",
            "interval": retry_after,
            "request_id": request_id,
        },
    }


def _same_device_flow(
    *,
    provider_key: str,
    device_code: str,
    auth_session_id: str | None,
    flow: dict[str, Any],
) -> bool:
    supplied_session_id = str(auth_session_id or "").strip()
    return (
        str(flow.get("provider") or "") == provider_key
        and str(flow.get("device_code_hash") or "") == _device_code_hash(device_code)
        and (not supplied_session_id or supplied_session_id == str(flow.get("auth_session_id") or ""))
    )


def _claim_poll_from_flow(
    *,
    provider_key: str,
    device_code: str,
    auth_session_id: str | None,
    request_id: str,
    flow: dict[str, Any],
    now: float,
) -> dict[str, Any]:
    if not _same_device_flow(
        provider_key=provider_key,
        device_code=device_code,
        auth_session_id=auth_session_id,
        flow=flow,
    ):
        return _stale_device_code_response(provider_key, request_id, flow)
    interval = max(1, int(flow.get("poll_interval") or 5))
    in_flight_until = float(flow.get("poll_in_flight_until") or 0.0)
    if in_flight_until > now:
        retry_after = max(1, int(min(interval, in_flight_until - now) + 0.999))
        return _poll_wait_response(
            provider_key=provider_key,
            auth_session_id=auth_session_id,
            flow=flow,
            request_id=request_id,
            error_type="poll_in_flight",
            retry_after=retry_after,
        )
    last_poll_at = float(flow.get("last_poll_at") or 0.0)
    elapsed = now - last_poll_at
    if last_poll_at and elapsed < interval:
        retry_after = max(1, int(interval - elapsed + 0.999))
        return _poll_wait_response(
            provider_key=provider_key,
            auth_session_id=auth_session_id,
            flow=flow,
            request_id=request_id,
            error_type="poll_interval_not_elapsed",
            retry_after=retry_after,
        )
    return {"ok": True, "auth_session_id": str(flow.get("auth_session_id") or auth_session_id or "")}


async def claim_device_auth_poll(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    request_id: str,
    db: Any = None,
) -> dict[str, Any]:
    validation = await validate_device_auth_flow(
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
        claimed = _claim_poll_from_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            request_id=request_id,
            flow=flow,
            now=now,
        )
        if not claimed.get("ok"):
            return claimed
        session.last_poll_at = _unix_to_utc(now)
        session.poll_in_flight_until = _unix_to_utc(now + 35.0)
        session.updated_at = _utcnow()
        await db.commit()
        return claimed

    async with DEVICE_AUTH_LOCK:
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
        if not flow:
            return validation
        claimed = _claim_poll_from_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            request_id=request_id,
            flow=flow,
            now=now,
        )
        if not claimed.get("ok"):
            return claimed
        flow["last_poll_at"] = now
        flow["poll_in_flight_until"] = now + 35.0
        return claimed


async def release_device_auth_poll(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    db: Any = None,
) -> None:
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if flow and session and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        ):
            session.poll_in_flight_until = None
            session.updated_at = _utcnow()
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
        if flow and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        ):
            flow["poll_in_flight_until"] = 0.0


async def update_device_auth_poll_interval(
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
        if flow and session and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        ):
            session.poll_interval = max(1, int(interval or flow.get("poll_interval") or 5))
            session.updated_at = _utcnow()
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        flow = DEVICE_AUTH_FLOWS.get(_device_auth_key(user_id, provider_key))
        if flow and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        ):
            flow["poll_interval"] = max(1, int(interval or flow.get("poll_interval") or 5))


async def is_device_auth_flow_current(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    db: Any = None,
) -> bool:
    flow, _ = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id)
    return bool(
        flow
        and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        )
    )


async def clear_device_auth_flow(
    *,
    provider_key: str,
    user_id: Any,
    device_code: str,
    auth_session_id: str | None,
    db: Any = None,
) -> None:
    if _is_real_db_session(db):
        flow, session = await _load_device_auth_flow(db=db, provider_key=provider_key, user_id=user_id, for_update=True)
        if flow and session and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        ):
            await db.delete(session)
            await db.commit()
        return

    async with DEVICE_AUTH_LOCK:
        key = _device_auth_key(user_id, provider_key)
        flow = DEVICE_AUTH_FLOWS.get(key)
        if flow and _same_device_flow(
            provider_key=provider_key,
            device_code=device_code,
            auth_session_id=auth_session_id,
            flow=flow,
        ):
            DEVICE_AUTH_FLOWS.pop(key, None)


async def clear_device_auth_flow_for_provider(*, provider_key: str, user_id: Any, db: Any = None) -> None:
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
