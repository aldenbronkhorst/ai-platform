"""Generic connected-account lifecycle for self-describing connector packages."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIConnectedAccount
from app.services.connected_account_state import effective_connected_accounts
from app.services.external_connectors import (
    ConnectorRequestError,
    configured_connector_ids,
    connector_endpoint,
    load_connector_manifest,
    verify_connector_values,
)
from app.services.key_vault import delete_secret, key_vault_uri, set_secret_value


router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])
logger = logging.getLogger(__name__)


class ConnectorConnectRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _secret_name(account_id: UUID) -> str:
    return f"connected-account-{account_id}-{uuid.uuid4().hex[:12]}-secret"


def _fields(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    fields = manifest.get("connection_fields")
    return [field for field in fields if isinstance(field, dict) and field.get("name")] if isinstance(fields, list) else []


def _split_values(manifest: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    configuration: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for field in _fields(manifest):
        name = str(field["name"])
        value = values.get(name)
        if field.get("required") and (value is None or (isinstance(value, str) and not value.strip())):
            raise HTTPException(
                status_code=400,
                detail={
                    "error_type": "connector_field_required",
                    "field": name,
                    "message": f"{field.get('label') or name} is required.",
                },
            )
        if value in (None, ""):
            continue
        target = secrets if field.get("secret") else configuration
        target[name] = value
    return configuration, secrets


def _safe_configuration(manifest: dict[str, Any], values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    allowed = {str(field["name"]) for field in _fields(manifest) if not field.get("secret")}
    return {key: value for key, value in values.items() if key in allowed}


async def _account(db: AsyncSession, user_id: UUID, connector_id: str) -> AIConnectedAccount | None:
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == connector_id,
        )
    )
    return result.scalar_one_or_none()


def _status(account: AIConnectedAccount | None) -> str:
    if account is None or account.status in {"disconnected", "not_connected"}:
        return "not_connected"
    return str(account.status)


def _account_payload(
    connector_id: str,
    manifest: dict[str, Any],
    account: AIConnectedAccount | None,
) -> dict[str, Any]:
    status = _status(account)
    return {
        "connector_key": connector_id,
        "display_name": manifest.get("display_name") or connector_id.replace("_", " ").title(),
        "subtitle": manifest.get("subtitle") or "Connector",
        "version": manifest.get("version"),
        "status": status,
        "auth_method": manifest.get("auth_method"),
        "last_verified_at": account.last_verified_at.isoformat() if account and account.last_verified_at else None,
        "actions_available": ["connect"] if status == "not_connected" else ["disconnect"],
        "state": {
            "configured": status not in {"not_connected", "disconnected"},
            "account_status": status,
            "source": "database",
        },
        "manifest": manifest,
        "configuration": dict(account.configuration_json or {}) if account else {},
        "metadata": dict(account.connector_metadata_json or {}) if account else {},
        "identity": {
            "id": account.provider_user_id,
            "username": account.provider_username,
            "display_name": account.provider_display_name,
        } if account else {},
        "account_id": str(account.id) if account else None,
        "target_environment": account.target_environment if account else None,
    }


async def _store_secret(name: str, values: dict[str, Any]) -> None:
    if not key_vault_uri():
        raise HTTPException(
            status_code=500,
            detail={"error_type": "key_vault_unavailable", "message": "Secure credential storage is not configured."},
        )
    try:
        await set_secret_value(name, json.dumps(values, separators=(",", ":"), ensure_ascii=False))
    except Exception as exc:
        logger.exception("Could not store connected-account credentials")
        raise HTTPException(
            status_code=500,
            detail={"error_type": "credential_storage_failed", "message": "Could not save connector credentials securely."},
        ) from exc


async def _delete_secret(name: str | None) -> None:
    if not name or not key_vault_uri():
        return
    try:
        await delete_secret(name)
    except Exception as exc:
        if "SecretNotFound" not in str(exc) and "NotFound" not in str(exc):
            logger.warning("Could not delete connected-account secret %s: %s", name, exc)


@router.get("")
async def get_connected_accounts(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    user_id = auth.get("user_id")
    accounts = {account.provider: account for account in await effective_connected_accounts(db, user_id)}
    connectors: list[dict[str, Any]] = []
    for connector_id in configured_connector_ids():
        try:
            manifest = await load_connector_manifest(connector_id)
            connectors.append(_account_payload(connector_id, manifest, accounts.get(connector_id)))
        except Exception as exc:
            logger.warning("Connector manifest unavailable | connector=%s error=%s", connector_id, exc)
            connectors.append({
                "connector_key": connector_id,
                "display_name": connector_id.replace("_", " ").title(),
                "subtitle": "Connector unavailable",
                "status": "unavailable",
                "actions_available": [],
                "state": {"configured": False, "account_status": "unavailable", "source": "registration"},
                "manifest": None,
                "configuration": {},
                "metadata": {},
                "identity": {},
                "error": str(exc),
            })
    return {"connectors": connectors}


@router.get("/{connector_id}/status")
async def get_connector_status(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    if connector_endpoint(connector_id) is None:
        raise HTTPException(status_code=404, detail="Connector is not configured.")
    manifest = await load_connector_manifest(connector_id)
    return _account_payload(connector_id, manifest, await _account(db, auth.get("user_id"), connector_id))


@router.post("/{connector_id}/connect")
async def connect_account(
    connector_id: str,
    req: ConnectorConnectRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    if connector_endpoint(connector_id) is None:
        raise HTTPException(status_code=404, detail="Connector is not configured.")
    manifest = await load_connector_manifest(connector_id)
    submitted_configuration, submitted_secrets = _split_values(manifest, req.values)
    try:
        verification = await verify_connector_values(connector_id, req.values)
    except ConnectorRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        logger.exception("Connector verification failed | connector=%s", connector_id)
        raise HTTPException(
            status_code=502,
            detail={"error_type": "connector_unreachable", "message": "Could not reach the connector service."},
        ) from exc

    user_id = auth.get("user_id")
    existing = await _account(db, user_id, connector_id)
    account_id = existing.id if existing else uuid.uuid4()
    previous_secret = existing.secret_reference if existing else None
    next_secret = _secret_name(account_id) if submitted_secrets else None
    if next_secret:
        await _store_secret(next_secret, submitted_secrets)

    identity = verification.get("identity") if isinstance(verification.get("identity"), dict) else {}
    metadata = verification.get("metadata") if isinstance(verification.get("metadata"), dict) else {}
    verified_configuration = _safe_configuration(manifest, verification.get("configuration"))
    configuration = verified_configuration or submitted_configuration
    now = _utcnow()
    account = existing or AIConnectedAccount(id=account_id, user_id=user_id, provider=connector_id)
    if existing is None:
        db.add(account)
    account.provider_user_id = str(identity.get("id")) if identity.get("id") is not None else None
    account.provider_username = str(identity.get("username")) if identity.get("username") else None
    account.provider_display_name = str(identity.get("display_name")) if identity.get("display_name") else None
    account.status = "connected"
    account.secret_reference = next_secret
    account.configuration_json = configuration
    account.connector_metadata_json = metadata
    account.last_verified_at = now
    account.disconnected_at = None
    account.updated_at = now
    account.target_environment = str(verification.get("target_environment") or account.target_environment or "production")

    try:
        await db.commit()
        await db.refresh(account)
    except Exception:
        await _delete_secret(next_secret)
        raise
    if previous_secret and previous_secret != next_secret:
        await _delete_secret(previous_secret)
    return _account_payload(connector_id, manifest, account)


@router.delete("/{connector_id}")
async def disconnect_account(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    account = await _account(db, auth.get("user_id"), connector_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Connected account not found.")
    await _delete_secret(account.secret_reference)
    account.status = "disconnected"
    account.secret_reference = None
    account.provider_user_id = None
    account.provider_username = None
    account.provider_display_name = None
    account.permission_summary = None
    account.last_verified_at = None
    account.disconnected_at = _utcnow()
    account.configuration_json = None
    account.connector_metadata_json = None
    account.updated_at = _utcnow()
    await db.commit()
    return {"connector_key": connector_id, "status": "disconnected"}
