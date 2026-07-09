import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIConnectedAccount
from app.services.connected_account_state import effective_connected_accounts
from app.services.key_vault import delete_secret, key_vault_uri, set_secret_value

router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])
logger = logging.getLogger(__name__)

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")

DNS_FAILURE_PHRASES = [
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "no address associated with hostname",
    "no such host is known",
]


class ConnectErrorDetail(BaseModel):
    error_type: str = ""
    message: str = ""
    request_id: str = ""


class OdooConnectRequest(BaseModel):
    odoo_url: str = Field(..., description="Odoo instance URL")
    odoo_db: str = Field(..., description="Odoo database name")
    odoo_username: str = Field(..., description="Odoo username")
    odoo_api_key: str = Field(..., description="Odoo API key or password")


class ConnectedAccountResponse(BaseModel):
    id: UUID
    user_id: UUID
    provider: str
    provider_username: Optional[str]
    status: str
    last_verified_at: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    disconnected_at: Optional[datetime]
    target_environment: str
    odoo_url: Optional[str] = None
    odoo_db: Optional[str] = None
    odoo_company_id: Optional[int] = None
    odoo_company_name: Optional[str] = None
    odoo_currency_code: Optional[str] = None
    odoo_currency_symbol: Optional[str] = None


class OdooStatusResponse(BaseModel):
    status: str
    provider_username: Optional[str] = None
    last_verified_at: Optional[datetime] = None
    target_environment: Optional[str] = None
    account_id: Optional[UUID] = None
    odoo_url: Optional[str] = None
    odoo_db: Optional[str] = None
    odoo_company_id: Optional[int] = None
    odoo_company_name: Optional[str] = None
    odoo_currency_code: Optional[str] = None
    odoo_currency_symbol: Optional[str] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _normalize_odoo_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = url.rstrip("/")
    if not re.match(r"^https?://[a-zA-Z0-9.-]+", parsed):
        raise HTTPException(status_code=400, detail="Invalid Odoo URL format.")
    return parsed


def _generate_secret_name(account_id: UUID) -> str:
    return f"connected-account-{str(account_id)}-{uuid.uuid4().hex[:12]}-secret"


def _classify_odoo_error(error_str: str, status_code: int = 400) -> str:
    del status_code
    lower = error_str.lower()
    if "database" in lower and "does not exist" in lower:
        return "odoo_database_not_found"
    if "oid does not exist" in lower or ("role" in lower and "does not exist" in lower):
        return "odoo_authentication_failed"
    if "access denied" in lower or "access error" in lower:
        return "odoo_permission_error"
    if "authentication failed" in lower or "wrong password" in lower or "invalid password" in lower:
        return "odoo_authentication_failed"
    if "ssl" in lower:
        return "odoo_ssl_error"
    if "timeout" in lower:
        return "odoo_timeout"
    return "unknown_odoo_error"


def _raise_connect_error(*, status_code: int, error_type: str, message: str, request_id: str = "") -> None:
    raise HTTPException(
        status_code=status_code,
        detail=ConnectErrorDetail(error_type=error_type, message=message, request_id=request_id).model_dump(),
    )


def _odoo_headers(request_id: str = "") -> dict[str, str]:
    headers = {
        "X-Internal-API-Key": ODOO_CONNECTOR_KEY,
        "Content-Type": "application/json",
    }
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _odoo_payload(url: str, db: str, username: str, api_key: str, model: str, fields: list[str]) -> dict:
    return {
        "credentials": {"url": url, "db": db, "username": username, "api_key": api_key},
        "model": model,
        "method": "search_read",
        "args": [[]],
        "kwargs": {"fields": fields, "limit": 1},
    }


async def _post_connector(payload: dict, request_id: str = "", timeout: float = 30.0) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(
            f"{ODOO_CONNECTOR_URL.rstrip('/')}/odoo/orm/run",
            json=payload,
            headers=_odoo_headers(request_id),
        )


def _is_dns_failure(error_str: str) -> bool:
    return any(phrase in error_str.lower() for phrase in DNS_FAILURE_PHRASES)


def _response_error_body(response: httpx.Response) -> dict:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def _connector_error_message(response: httpx.Response) -> str:
    body = _response_error_body(response)
    return str(body.get("message", body.get("detail", body)))


def _connector_error_code(err_body: dict) -> str | None:
    """Read the connector's error code whether it is top-level or nested.

    The connector raises FastAPI HTTPException, so its payload is wrapped under
    'detail' (e.g. {"detail": {"error": "odoo_auth_failed", ...}}); its app-level
    handler returns the code top-level. A bad *internal* API key yields a plain
    string detail, so this returns None for it (correctly NOT an Odoo-auth error).
    """
    detail = err_body.get("detail")
    if isinstance(detail, dict) and detail.get("error"):
        return detail.get("error")
    return err_body.get("error")


def _raise_verify_response_error(response: httpx.Response, request_id: str = "") -> None:
    err_body = _response_error_body(response)
    raw_detail = str(err_body.get("message", err_body.get("detail", str(err_body))))
    classified = _classify_odoo_error(raw_detail, response.status_code)
    error_code = _connector_error_code(err_body)

    # Bad Odoo credentials (connector 401/400 with error 'odoo_auth_failed') must be
    # reported as invalid Odoo creds, NOT as an internal connector-key mismatch.
    if response.status_code == 401:
        if error_code == "odoo_auth_failed":
            _raise_connect_error(
                status_code=400,
                error_type=classified,
                message="Odoo credentials are invalid. Check your URL, database, username, and API key.",
                request_id=request_id,
            )
        internal_detail = f"Connector returned 401: {raw_detail}"
        logger.error("Odoo connector authentication failed | request_id=%s detail=%s", request_id, internal_detail)
        _raise_connect_error(
            status_code=401,
            error_type="odoo_connector_auth_failed",
            message="Internal connector API key mismatch. Contact an administrator.",
            request_id=request_id,
        )

    if response.status_code == 400 and error_code == "odoo_auth_failed":
        _raise_connect_error(
            status_code=400,
            error_type=classified,
            message="Odoo credentials are invalid. Check your URL, database, username, and API key.",
            request_id=request_id,
        )

    logger.info(
        "Odoo verification returned error | request_id=%s status=%s error_type=%s detail=%s",
        request_id, response.status_code, classified, raw_detail,
    )
    _raise_connect_error(
        status_code=400,
        error_type=_classify_odoo_error(raw_detail),
        message="Odoo verification failed. Check permissions or contact support.",
        request_id=request_id,
    )


async def _verify_odoo_credentials_via_connector(url: str, db: str, username: str, api_key: str, request_id: str) -> None:
    if not ODOO_CONNECTOR_URL:
        _raise_connect_error(
            status_code=500,
            error_type="odoo_connector_url_missing",
            message="Odoo Connector is not configured.",
            request_id=request_id,
        )

    try:
        response = await _post_connector(
            _odoo_payload(url, db, username, api_key, "res.partner", ["id"]),
            request_id,
        )
    except httpx.ConnectError as exc:
        error_str = str(exc)
        _raise_connect_error(
            status_code=502,
            error_type="odoo_connector_dns_failed" if _is_dns_failure(error_str) else "odoo_connector_unreachable",
            message=(
                "The AI Platform API could not resolve the Odoo Connector service hostname."
                if _is_dns_failure(error_str)
                else "Could not reach the Odoo Connector service. Check network connectivity."
            ),
            request_id=request_id,
        )
    except httpx.TimeoutException:
        _raise_connect_error(
            status_code=504,
            error_type="odoo_timeout",
            message="Odoo Connector timed out. Check network connectivity.",
            request_id=request_id,
        )
    except httpx.RequestError:
        _raise_connect_error(
            status_code=502,
            error_type="odoo_connector_unreachable",
            message="Could not connect to Odoo Connector.",
            request_id=request_id,
        )

    if response.status_code >= 400:
        _raise_verify_response_error(response, request_id)


async def _fetch_odoo_company_metadata(url: str, db: str, username: str, api_key: str) -> dict:
    if not ODOO_CONNECTOR_URL:
        return {}
    try:
        response = await _post_connector(
            _odoo_payload(url, db, username, api_key, "res.company", ["id", "name", "currency_id"]),
            timeout=30.0,
        )
        if response.status_code >= 400:
            logger.warning("Failed to fetch company metadata from Odoo: %s", response.text)
            return {}
        data = response.json()
        records = data.get("result") if isinstance(data, dict) else data
        if not isinstance(records, list) or not records:
            return {}
        company = records[0]
        currency_data = company.get("currency_id")
        currency_code = None
        if isinstance(currency_data, dict):
            currency_code = currency_data.get("name")
        elif isinstance(currency_data, list) and len(currency_data) >= 2:
            currency_code = str(currency_data[1]) if currency_data[1] else None
        elif isinstance(currency_data, str):
            currency_code = currency_data
        return {
            "odoo_company_id": company.get("id"),
            "odoo_company_name": company.get("name") or company.get("display_name", ""),
            "odoo_currency_code": currency_code,
            "odoo_currency_symbol": {"ZAR": "R", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency_code, currency_code),
        }
    except Exception as exc:
        logger.warning("Could not fetch Odoo company metadata: %s", exc)
        return {}


async def _store_key_vault_secret(secret_name: str, secret_value: str) -> None:
    if not key_vault_uri():
        _raise_connect_error(
            status_code=500,
            error_type="key_vault_write_failed",
            message="Key Vault is not configured. Credentials cannot be stored securely.",
        )
    try:
        await set_secret_value(secret_name, secret_value)
    except Exception as exc:
        error_str = str(exc)
        message = (
            "Could not save connection credentials because a previously deleted secret is still reserved. Please retry."
            if "ObjectIsDeletedButRecoverable" in error_str or "Conflict" in error_str
            else "Failed to save connection credentials securely. Please try again."
        )
        logger.error("Failed to store connection secret in Key Vault.")
        _raise_connect_error(status_code=500, error_type="key_vault_write_failed", message=message)


async def _delete_key_vault_secret(secret_name: str) -> None:
    if not key_vault_uri():
        return
    try:
        await delete_secret(secret_name)
    except Exception as exc:
        if "SecretNotFound" not in str(exc) and "NotFound" not in str(exc):
            logger.error("Failed to delete connection secret from Key Vault.")


async def _existing_odoo_account(db: AsyncSession, user_id) -> Optional[AIConnectedAccount]:
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == "odoo",
        )
    )
    return result.scalar_one_or_none()


def _apply_company_metadata(account: AIConnectedAccount, metadata: dict) -> None:
    if not metadata.get("odoo_company_id"):
        return
    account.odoo_company_id = metadata["odoo_company_id"]
    account.odoo_company_name = metadata.get("odoo_company_name")
    account.odoo_currency_code = metadata.get("odoo_currency_code")
    account.odoo_currency_symbol = metadata.get("odoo_currency_symbol")


def _upsert_odoo_account(
    db: AsyncSession,
    *,
    existing_account: Optional[AIConnectedAccount],
    account_id: UUID,
    user_id,
    req: OdooConnectRequest,
    normalized_url: str,
    secret_name: str,
    verified: bool,
    company_meta: dict,
) -> AIConnectedAccount:
    now = _utcnow()
    account = existing_account or AIConnectedAccount(
        id=account_id,
        user_id=user_id,
        provider="odoo",
        created_at=now,
        target_environment="production",
    )
    if not existing_account:
        db.add(account)
    account.provider_username = req.odoo_username
    account.secret_reference = secret_name
    account.status = "connected" if verified else "error"
    account.last_verified_at = now if verified else account.last_verified_at
    account.disconnected_at = None
    account.updated_at = now
    account.odoo_url = normalized_url
    account.odoo_db = req.odoo_db
    _apply_company_metadata(account, company_meta)
    return account


def _account_status(account: Optional[AIConnectedAccount]) -> str:
    if not account or account.status in ("disconnected", "not_connected"):
        return "not_connected"
    return account.status


def _account_last_verified(account: Optional[AIConnectedAccount]) -> Optional[str]:
    return account.last_verified_at.isoformat() if account and account.last_verified_at else None


def _connector_state(account: Optional[AIConnectedAccount]) -> dict:
    status = _account_status(account)
    return {
        "configured": status not in {"not_connected", "disconnected"},
        "account_status": status,
        "token_status": "not_applicable",
        "source": "database",
    }


@router.get("")
async def get_connected_accounts(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
    include_token_state: bool = Query(False),
):
    del include_token_state
    user_id = auth.get("user_id")
    db_accounts = await effective_connected_accounts(db, user_id)
    odoo = next((account for account in db_accounts if account.provider == "odoo"), None)
    return {
        "connectors": [
            {
                "connector_key": "odoo",
                "display_name": "Odoo Enterprise",
                "subtitle": "ERP connector",
                "status": _account_status(odoo),
                "auth_method": "api_key",
                "last_verified_at": _account_last_verified(odoo),
                "actions_available": ["connect"] if _account_status(odoo) == "not_connected" else ["disconnect"],
                "state": _connector_state(odoo),
                "metadata": {
                    "odoo_url": odoo.odoo_url if odoo else None,
                    "odoo_db": odoo.odoo_db if odoo else None,
                    "provider_username": odoo.provider_username if odoo else None,
                } if odoo else {},
            }
        ]
    }


@router.post("/odoo/connect")
async def connect_odoo(
    req: OdooConnectRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    user_id = auth.get("user_id")
    request_id = _get_request_id()
    normalized_url = _normalize_odoo_url(req.odoo_url)
    existing_account = await _existing_odoo_account(db, user_id)
    account_id = existing_account.id if existing_account else uuid.uuid4()
    secret_name = _generate_secret_name(account_id)

    await _store_key_vault_secret(secret_name, req.odoo_api_key)

    verified = True
    verify_error: dict | None = None
    company_meta: dict = {}
    try:
        await _verify_odoo_credentials_via_connector(
            normalized_url,
            req.odoo_db,
            req.odoo_username,
            req.odoo_api_key,
            request_id,
        )
        company_meta = await _fetch_odoo_company_metadata(
            normalized_url,
            req.odoo_db,
            req.odoo_username,
            req.odoo_api_key,
        )
    except HTTPException as exc:
        verified = False
        verify_error = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}

    account = _upsert_odoo_account(
        db,
        existing_account=existing_account,
        account_id=account_id,
        user_id=user_id,
        req=req,
        normalized_url=normalized_url,
        secret_name=secret_name,
        verified=verified,
        company_meta=company_meta,
    )
    await db.commit()
    await db.refresh(account)

    if not verified:
        _raise_connect_error(
            status_code=400,
            error_type=(verify_error or {}).get("error_type") or "odoo_credentials_invalid",
            message=(verify_error or {}).get("message") or "Odoo credentials could not be verified.",
            request_id=request_id,
        )
    return account


@router.get("/odoo/status", response_model=OdooStatusResponse)
async def get_odoo_status(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    account = await _existing_odoo_account(db, auth.get("user_id"))
    if not account or account.status == "disconnected":
        return OdooStatusResponse(status="not_connected")
    return OdooStatusResponse(
        status=account.status,
        provider_username=account.provider_username,
        last_verified_at=account.last_verified_at,
        target_environment=account.target_environment,
        account_id=account.id,
        odoo_url=account.odoo_url,
        odoo_db=account.odoo_db,
        odoo_company_id=account.odoo_company_id,
        odoo_company_name=account.odoo_company_name,
        odoo_currency_code=account.odoo_currency_code,
        odoo_currency_symbol=account.odoo_currency_symbol,
    )


@router.post("/odoo/disconnect", response_model=ConnectedAccountResponse)
async def disconnect_odoo(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    account = await _existing_odoo_account(db, auth.get("user_id"))
    if not account:
        raise HTTPException(status_code=404, detail="Odoo connected account not found.")

    if account.secret_reference:
        await _delete_key_vault_secret(account.secret_reference)

    account.status = "disconnected"
    account.secret_reference = None
    account.provider_username = None
    account.provider_user_id = None
    account.provider_display_name = None
    account.permission_summary = None
    account.last_verified_at = None
    account.disconnected_at = _utcnow()
    account.updated_at = _utcnow()
    account.odoo_url = None
    account.odoo_db = None
    account.odoo_company_id = None
    account.odoo_company_name = None
    account.odoo_currency_code = None
    account.odoo_currency_symbol = None

    await db.commit()
    await db.refresh(account)
    return account
