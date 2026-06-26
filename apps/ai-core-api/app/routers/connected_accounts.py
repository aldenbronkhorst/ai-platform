import os
import logging
import httpx
import uuid
import re
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import AIConnectedAccount
from app.services.key_vault import delete_secret, key_vault_uri, set_secret_value
from app.services.connected_account_state import DELEGATED_TOKEN_PROVIDERS, effective_connected_accounts
from app.services.connectors.microsoft_admin.constants import (
    MICROSOFT_NATIVE_CONNECTOR_PROFILES,
    microsoft_native_app_name_for_provider,
    microsoft_native_label_for_provider,
)

router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")

logger = logging.getLogger(__name__)

DNS_FAILURE_PHRASES = [
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "no address associated with hostname",
    "no such host is known",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _classify_odoo_error(error_str: str, status_code: int = 400) -> str:
    """Classify an Odoo error message into a structured error type."""
    lower = error_str.lower()
    if "database" in lower and "does not exist" in lower:
        return "odoo_database_not_found"
    if "oid does not exist" in lower or "role" in lower and "does not exist" in lower:
        return "odoo_authentication_failed"
    if "access denied" in lower or "access error" in lower:
        return "odoo_permission_error"
    if "authentication failed" in lower or "wrong password" in lower or "invalid password" in lower:
        return "odoo_authentication_failed"
    if "ssl" in lower:
        return "odoo_ssl_error"
    if "timeout" in lower:
        return "odoo_timeout"
    if "transport" in lower:
        return "odoo_transport_error"
    return "unknown_odoo_error"


class ConnectErrorDetail(BaseModel):
    error_type: str = ""
    message: str = ""
    request_id: str = ""


def _normalize_odoo_url(raw: str) -> str:
    """Normalize an Odoo URL: trim, add https:// if missing, remove trailing slash."""
    url = raw.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    # Remove www. prefix if present (common mistake)
    # Validate hostname is present
    parsed = url.rstrip("/")
    if not re.match(r"^https?://[a-zA-Z0-9.-]+", parsed):
        raise HTTPException(status_code=400, detail="Invalid Odoo URL format.")
    return parsed


def _generate_secret_name(account_id: UUID) -> str:
    """Generate a unique Key Vault secret name for a connected account.
    Uses a random suffix to avoid collisions with soft-deleted secrets."""
    random_suffix = uuid.uuid4().hex[:12]
    return f"connected-account-{str(account_id)}-{random_suffix}-secret"


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


def _account_status(account: Optional[AIConnectedAccount]) -> str:
    if not account or account.status in ("disconnected", "not_connected"):
        return "not_connected"
    return account.status


def _account_last_verified(account: Optional[AIConnectedAccount]) -> Optional[str]:
    return account.last_verified_at.isoformat() if account and account.last_verified_at else None


def _is_configured(account: Optional[AIConnectedAccount]) -> bool:
    return _account_status(account) not in {"not_connected", "disconnected"}


def _connector_state(account: Optional[AIConnectedAccount], provider: str, include_token_state: bool) -> dict:
    token_status = getattr(account, "token_status", None)
    return {
        "configured": _is_configured(account),
        "account_status": _account_status(account),
        "token_status": token_status or ("not_checked" if provider in DELEGATED_TOKEN_PROVIDERS else "not_applicable"),
        "source": "token_store" if include_token_state and provider in DELEGATED_TOKEN_PROVIDERS else "database",
    }


async def _fetch_odoo_company_metadata(url: str, db: str, username: str, api_key: str) -> dict:
    """Fetch company currency and company name from Odoo via the connector."""
    if not ODOO_CONNECTOR_URL:
        return {}
    try:
        headers = {
            "X-Internal-API-Key": ODOO_CONNECTOR_KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "credentials": {
                "url": url,
                "db": db,
                "username": username,
                "api_key": api_key,
                "transport": "auto",
            },
            "model": "res.company",
            "method": "search_read",
            "args": [[]],
            "kwargs": {
                "fields": ["id", "name", "currency_id"],
                "limit": 1,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ODOO_CONNECTOR_URL.rstrip('/')}/odoo/orm/run",
                json=payload,
                headers=headers,
            )
        if response.status_code >= 400:
            logger.warning("Failed to fetch company metadata from Odoo: %s", response.text)
            return {}

        data = response.json()
        records = data.get("result") if isinstance(data, dict) else data
        if isinstance(records, list) and len(records) > 0:
            company = records[0]
            company_id = company.get("id")
            company_name = company.get("name") or company.get("display_name", "")
            currency_data = company.get("currency_id")
            currency_code = None
            currency_symbol = None
            if isinstance(currency_data, dict):
                currency_code = currency_data.get("name")
            elif isinstance(currency_data, list) and len(currency_data) >= 2:
                currency_code = str(currency_data[1]) if currency_data[1] else None
            elif isinstance(currency_data, str):
                currency_code = currency_data

            if currency_code:
                currency_symbol = {
                    "ZAR": "R",
                    "USD": "$",
                    "EUR": "€",
                    "GBP": "£",
                    "JPY": "¥",
                    "AUD": "A$",
                    "CAD": "C$",
                }.get(currency_code, currency_code)
            return {
                "odoo_company_id": company_id,
                "odoo_company_name": company_name,
                "odoo_currency_code": currency_code,
                "odoo_currency_symbol": currency_symbol,
            }
        return {}
    except Exception as exc:
        logger.warning("Could not fetch Odoo company metadata: %s", exc)
        return {}


async def _verify_odoo_credentials_via_connector(
    url: str, db: str, username: str, api_key: str,
    request_id: str = "",
) -> None:
    """Uses the Odoo Connector API to perform a safe read-only call to verify credentials.

    Raises HTTPException with structured ConnectErrorDetail on failure.
    """
    logger.info("Verifying Odoo credentials for user=%s at host=%s db=%s", username, url, db)
    if not ODOO_CONNECTOR_URL:
        _raise_connect_error(
            status_code=500,
            error_type="odoo_connector_url_missing",
            message="Odoo Connector is not configured.",
            request_id=request_id,
        )

    try:
        response = await _post_odoo_verify_request(url, db, username, api_key, request_id)
    except httpx.ConnectError as e:
        _raise_connector_connect_error(e, request_id)
    except httpx.TimeoutException as e:
        logger.warning("Odoo connector timed out | request_id=%s error=%s", request_id, e)
        _raise_connect_error(
            status_code=504,
            error_type="odoo_timeout",
            message="Odoo Connector timed out. Check network connectivity.",
            request_id=request_id,
        )
    except httpx.RequestError as e:
        logger.warning("Odoo connector request failed | request_id=%s error=%s", request_id, e)
        _raise_connect_error(
            status_code=502,
            error_type="odoo_connector_unreachable",
            message="Could not connect to Odoo Connector.",
            request_id=request_id,
        )

    if response.status_code >= 400:
        _raise_verify_response_error(response, request_id)


def _raise_connect_error(
    *,
    status_code: int,
    error_type: str,
    message: str,
    request_id: str = "",
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=ConnectErrorDetail(
            error_type=error_type,
            message=message,
            request_id=request_id,
        ).model_dump()
    )


def _odoo_verify_headers(request_id: str) -> dict:
    headers = {
        "X-Internal-API-Key": ODOO_CONNECTOR_KEY,
        "Content-Type": "application/json",
    }
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _odoo_verify_payload(url: str, db: str, username: str, api_key: str) -> dict:
    return {
        "credentials": {
            "url": url,
            "db": db,
            "username": username,
            "api_key": api_key,
            "transport": "auto"
        },
        "model": "res.partner",
        "method": "search_read",
        "args": [[]],
        "kwargs": {
            "fields": ["id"],
            "limit": 1,
        },
    }


async def _post_odoo_verify_request(
    url: str,
    db: str,
    username: str,
    api_key: str,
    request_id: str,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            f"{ODOO_CONNECTOR_URL.rstrip('/')}/odoo/orm/run",
            json=_odoo_verify_payload(url, db, username, api_key),
            headers=_odoo_verify_headers(request_id),
        )


def _is_dns_failure(error_str: str) -> bool:
    return any(phrase in error_str.lower() for phrase in DNS_FAILURE_PHRASES)


def _raise_connector_connect_error(exc: httpx.ConnectError, request_id: str = "") -> None:
    error_str = str(exc)
    dns_failure = _is_dns_failure(error_str)
    err_type = "odoo_connector_dns_failed" if dns_failure else "odoo_connector_unreachable"
    err_msg = (
        "The AI Platform API could not resolve the Odoo Connector service hostname."
        if dns_failure
        else "Could not reach the Odoo Connector service. Check network connectivity."
    )
    logger.warning("Odoo connector connection failed | request_id=%s error_type=%s error=%s", request_id, err_type, error_str)
    _raise_connect_error(
        status_code=502,
        error_type=err_type,
        message=err_msg,
        request_id=request_id,
    )


def _response_error_body(response: httpx.Response) -> dict:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def _raise_odoo_auth_error(classified: str, err_body: dict, request_id: str = "") -> None:
    err_msg = str(err_body.get("detail", err_body))
    internal_detail = err_body.get("message", err_msg)
    logger.info("Odoo credential verification failed | request_id=%s error_type=%s detail=%s", request_id, classified, internal_detail)
    _raise_connect_error(
        status_code=400,
        error_type=classified,
        message="Odoo credentials are invalid. Check your URL, database, username, and API key.",
        request_id=request_id,
    )


def _raise_connector_auth_error(err_body: dict, request_id: str = "") -> None:
    err_msg = str(err_body.get("detail", err_body))
    internal_detail = f"Connector returned 401: {err_msg}"
    logger.error("Odoo connector authentication failed | request_id=%s detail=%s", request_id, internal_detail)
    _raise_connect_error(
        status_code=401,
        error_type="odoo_connector_auth_failed",
        message="Internal connector API key mismatch. Contact an administrator.",
        request_id=request_id,
    )


def _raise_verify_response_error(response: httpx.Response, request_id: str = "") -> None:
    err_body = _response_error_body(response)
    raw_detail = str(err_body.get("message", err_body.get("detail", str(err_body))))
    classified = _classify_odoo_error(raw_detail, response.status_code)

    if response.status_code == 401:
        if err_body.get("error") == "odoo_auth_failed":
            _raise_odoo_auth_error(classified, err_body, request_id)
        _raise_connector_auth_error(err_body, request_id)

    if response.status_code == 400 and err_body.get("error") == "odoo_auth_failed":
        _raise_odoo_auth_error(classified, err_body, request_id)

    logger.info(
        "Odoo verification returned error | request_id=%s status=%s error_type=%s detail=%s",
        request_id, response.status_code, classified, raw_detail,
    )
    _raise_connect_error(
        status_code=400,
        error_type=classified,
        message="Odoo verification failed. Check permissions or contact support.",
        request_id=request_id,
    )


async def _store_key_vault_secret(secret_name: str, secret_value: str) -> None:
    """Stores the secret in Azure Key Vault if Key Vault is configured.
    Raises HTTPException on failure, with a user-friendly message for
    ObjectIsDeletedButRecoverable conflicts.
    If KEY_VAULT_URI is not configured, raises so callers know storage failed."""
    if not key_vault_uri():
        raise HTTPException(
            status_code=500,
            detail=ConnectErrorDetail(
                error_type="key_vault_write_failed",
                message="Key Vault is not configured. Credentials cannot be stored securely.",
            ).model_dump()
        )

    try:
        await set_secret_value(secret_name, secret_value)
    except Exception as e:
        error_str = str(e)
        if "ObjectIsDeletedButRecoverable" in error_str or "Conflict" in error_str:
            logger.error(
                "Key Vault secret name collision (ObjectIsDeletedButRecoverable) "
                "for '%s': %s", secret_name, error_str
            )
            raise HTTPException(
                status_code=500,
                detail=ConnectErrorDetail(
                    error_type="key_vault_write_failed",
                    message="Could not save connection credentials because a previously "
                           "deleted secret is still reserved. Please retry, or contact "
                           "support if the issue persists.",
                ).model_dump()
            )
        logger.error("Failed to store secret '%s' in Key Vault: %s", secret_name, error_str)
        raise HTTPException(
            status_code=500,
            detail=ConnectErrorDetail(
                error_type="key_vault_write_failed",
                message="Failed to save connection credentials securely. Please try again.",
            ).model_dump()
        )


async def _delete_key_vault_secret(secret_name: str) -> None:
    """Deletes the secret in Azure Key Vault if Key Vault is configured.
    Does not raise if the secret doesn't exist (already deleted or never created)."""
    if not key_vault_uri():
        return

    try:
        await delete_secret(secret_name)
    except Exception as e:
        error_str = str(e)
        # If secret doesn't exist, just log and continue - don't fail the disconnect
        if "SecretNotFound" in error_str or "NotFound" in error_str:
            logger.warning(
                "Secret '%s' not found in Key Vault during disconnect (already deleted or never created)",
                secret_name
            )
            return
        # For other errors, log but don't raise - let the DB transaction proceed
        logger.error("Failed to delete secret '%s' from Key Vault: %s", secret_name, error_str)


async def _existing_odoo_account(db: AsyncSession, user_id) -> Optional[AIConnectedAccount]:
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == "odoo",
        )
    )
    return result.scalar_one_or_none()


async def _verify_odoo_connect_request(
    req: OdooConnectRequest,
    normalized_url: str,
    request_id: str,
) -> tuple[bool, dict, Optional[dict]]:
    logger.info(
        "Verifying Odoo via connector url=%s db=%s username=%s request_id=%s",
        normalized_url, req.odoo_db, req.odoo_username, request_id,
    )
    try:
        await _verify_odoo_credentials_via_connector(
            url=normalized_url,
            db=req.odoo_db,
            username=req.odoo_username,
            api_key=req.odoo_api_key,
            request_id=request_id,
        )
        company_meta = await _fetch_odoo_company_metadata(
            url=normalized_url,
            db=req.odoo_db,
            username=req.odoo_username,
            api_key=req.odoo_api_key,
        )
        return True, company_meta, None
    except HTTPException as e:
        return False, {}, e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
    except Exception as e:
        return False, {}, {"message": str(e)}


def _apply_odoo_company_metadata(account: AIConnectedAccount, company_meta: dict) -> None:
    if not company_meta.get("odoo_company_id"):
        return
    account.odoo_company_id = company_meta["odoo_company_id"]
    account.odoo_company_name = company_meta.get("odoo_company_name")
    account.odoo_currency_code = company_meta.get("odoo_currency_code")
    account.odoo_currency_symbol = company_meta.get("odoo_currency_symbol")


def _update_odoo_account(
    account: AIConnectedAccount,
    *,
    req: OdooConnectRequest,
    normalized_url: str,
    secret_name: str,
    verified: bool,
    company_meta: dict,
    now: datetime,
) -> AIConnectedAccount:
    account.provider_username = req.odoo_username
    account.secret_reference = secret_name
    account.status = "connected" if verified else "error"
    account.last_verified_at = now if verified else account.last_verified_at
    account.disconnected_at = None
    account.updated_at = now
    account.odoo_url = normalized_url
    account.odoo_db = req.odoo_db
    _apply_odoo_company_metadata(account, company_meta)
    return account


def _new_odoo_account(
    *,
    account_id: UUID,
    user_id,
    req: OdooConnectRequest,
    normalized_url: str,
    secret_name: str,
    verified: bool,
    company_meta: dict,
    now: datetime,
) -> AIConnectedAccount:
    return AIConnectedAccount(
        id=account_id,
        user_id=user_id,
        provider="odoo",
        provider_username=req.odoo_username,
        secret_reference=secret_name,
        status="connected" if verified else "error",
        last_verified_at=now if verified else None,
        target_environment="production",
        created_at=now,
        updated_at=now,
        odoo_url=normalized_url,
        odoo_db=req.odoo_db,
        odoo_company_id=company_meta.get("odoo_company_id"),
        odoo_company_name=company_meta.get("odoo_company_name"),
        odoo_currency_code=company_meta.get("odoo_currency_code"),
        odoo_currency_symbol=company_meta.get("odoo_currency_symbol"),
    )


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
    if existing_account:
        return _update_odoo_account(
            existing_account,
            req=req,
            normalized_url=normalized_url,
            secret_name=secret_name,
            verified=verified,
            company_meta=company_meta,
            now=now,
        )
    account = _new_odoo_account(
        account_id=account_id,
        user_id=user_id,
        req=req,
        normalized_url=normalized_url,
        secret_name=secret_name,
        verified=verified,
        company_meta=company_meta,
        now=now,
    )
    db.add(account)
    return account


def _verification_error_field(verify_error: Optional[dict], field: str, default: str) -> str:
    return (verify_error or {}).get(field) or default


def _raise_failed_odoo_connect(
    *,
    verify_error: Optional[dict],
    request_id: str,
    user_id,
) -> None:
    err_type = _verification_error_field(verify_error, "error_type", "odoo_credentials_invalid")
    err_msg = _verification_error_field(
        verify_error,
        "message",
        "Odoo credentials could not be verified. Your details have been saved with status 'error'.",
    )
    logger.warning(
        "Odoo connect failed request_id=%s user_id=%s error_type=%s detail=%s",
        request_id, user_id, err_type, verify_error,
    )
    raise HTTPException(
        status_code=400,
        detail=ConnectErrorDetail(
            error_type=err_type,
            message=err_msg,
            request_id=request_id,
        ).model_dump()
    )


def _log_successful_odoo_connect(request_id: str, user_id) -> None:
    logger.info(
        "Odoo connect succeeded request_id=%s user_id=%s",
        request_id, user_id,
    )


@router.post("/odoo/connect")
async def connect_odoo(
    req: OdooConnectRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Saves/creates Odoo connection.

    Flow:
    1. Normalize URL
    2. Store API key in Key Vault FIRST (if this fails, nothing is saved)
    3. Verify credentials against Odoo Connector
    4. If verification fails, save account with status=\"error\" to preserve user-entered details
    5. If verification succeeds, fetch metadata and save as status=\"connected\"
    """
    user_id = auth.get("user_id")
    request_id = _get_request_id()
    logger.info(
        "Odoo connect request received user_id=%s url=%s db=%s username=%s api_key_present=%s request_id=%s",
        user_id,
        req.odoo_url,
        req.odoo_db,
        req.odoo_username,
        bool(req.odoo_api_key),
        request_id,
    )

    normalized_url = _normalize_odoo_url(req.odoo_url)
    existing_account = await _existing_odoo_account(db, user_id)
    connected_account_id = existing_account.id if existing_account else uuid.uuid4()
    secret_name = _generate_secret_name(connected_account_id)

    await _store_key_vault_secret(secret_name, req.odoo_api_key)

    verified, company_meta, verify_error = await _verify_odoo_connect_request(
        req, normalized_url, request_id,
    )
    account = _upsert_odoo_account(
        db,
        existing_account=existing_account,
        account_id=connected_account_id,
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
        _raise_failed_odoo_connect(
            verify_error=verify_error,
            request_id=request_id,
            user_id=user_id,
        )

    _log_successful_odoo_connect(request_id, user_id)
    return account


@router.get("")
async def get_connected_accounts(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
    include_token_state: bool = Query(False),
):
    """Returns normalized connector metadata for all connectors for the authenticated user."""
    user_id = auth.get("user_id")
    db_accounts = await effective_connected_accounts(db, user_id, include_token_state=include_token_state)
    odoo = next((a for a in db_accounts if a.provider == "odoo"), None)
    github = next((a for a in db_accounts if a.provider == "github"), None)

    connectors = [
        {
            "connector_key": "odoo",
            "display_name": "Odoo Enterprise",
            "subtitle": "ERP connector",
            "status": _account_status(odoo),
            "auth_method": "api_key",
            "last_verified_at": _account_last_verified(odoo),
            "actions_available": ["connect", "test", "disconnect"] if _account_status(odoo) == "not_connected" else ["test", "disconnect"],
            "state": _connector_state(odoo, "odoo", include_token_state),
            "metadata": {
                "odoo_url": odoo.odoo_url if odoo else None,
                "odoo_db": odoo.odoo_db if odoo else None,
                "provider_username": odoo.provider_username if odoo else None,
            } if odoo else {},
        },
        *_microsoft_native_connector_cards(db_accounts, include_token_state),
        {
            "connector_key": "github",
            "display_name": "GitHub CLI",
            "subtitle": "Native GitHub CLI connector",
            "status": _account_status(github),
            "auth_method": "github_oauth",
            "last_verified_at": _account_last_verified(github),
            "actions_available": ["connect", "test", "disconnect"],
            "state": _connector_state(github, "github", include_token_state),
            "metadata": {
                "provider_username": github.provider_username if github else None,
                "permission_summary": github.permission_summary if github else None,
            } if github else {},
        },
    ]
    return {"connectors": connectors}


def _microsoft_native_connector_cards(db_accounts: list[AIConnectedAccount], include_token_state: bool) -> list[dict]:
    subtitles = {
        "azure_cli": "Native Azure CLI",
        "microsoft_graph": "Direct Microsoft Graph",
        "exchange_online": "Exchange Online PowerShell",
        "teams_admin": "Microsoft Teams PowerShell",
        "sharepoint_pnp": "SharePoint / PnP PowerShell",
    }
    tooling = {
        "azure_cli": ["Azure CLI"],
        "microsoft_graph": ["Direct Microsoft Graph"],
        "exchange_online": ["Exchange Online PowerShell"],
        "teams_admin": ["Microsoft Teams PowerShell"],
        "sharepoint_pnp": ["SharePoint / PnP PowerShell"],
    }
    cards: list[dict] = []
    for provider in MICROSOFT_NATIVE_CONNECTOR_PROFILES:
        account = next((a for a in db_accounts if a.provider == provider), None)
        cards.append(
            {
                "connector_key": provider,
                "display_name": microsoft_native_label_for_provider(provider),
                "subtitle": subtitles.get(provider, "Native Microsoft connector"),
                "status": _account_status(account),
                "auth_method": "native_microsoft",
                "last_verified_at": _account_last_verified(account),
                "actions_available": ["connect", "test", "disconnect"],
                "state": _connector_state(account, provider, include_token_state),
                "metadata": {
                    "provider_username": account.provider_username if account else None,
                    "permission_summary": account.permission_summary if account else None,
                    "tooling": tooling.get(provider, []),
                    "auth_app_name": microsoft_native_app_name_for_provider(provider),
                    "native_connector": True,
                } if account else {
                    "tooling": tooling.get(provider, []),
                    "auth_app_name": microsoft_native_app_name_for_provider(provider),
                    "native_connector": True,
                },
            }
        )
    return cards


@router.get("/odoo/status", response_model=OdooStatusResponse)
async def get_odoo_status(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Returns status of the Odoo connection for the authenticated user."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == "odoo",
        )
    )
    account = result.scalar_one_or_none()

    if not account or account.status == "disconnected":
        return OdooStatusResponse(
            status="not_connected",
            provider_username=None,
            last_verified_at=None,
            target_environment=None,
            account_id=None,
            odoo_url=None,
            odoo_db=None,
            odoo_company_id=None,
            odoo_company_name=None,
            odoo_currency_code=None,
            odoo_currency_symbol=None,
        )

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
    """Disconnects Odoo. Sets status to 'disconnected' and removes Key Vault secret."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == "odoo",
        )
    )
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(
            status_code=404,
            detail="Odoo connected account not found."
        )

    # 1. Delete Key Vault secret for security
    if account.secret_reference:
        await _delete_key_vault_secret(account.secret_reference)

    # 2. Clear all connection metadata and credentials
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
