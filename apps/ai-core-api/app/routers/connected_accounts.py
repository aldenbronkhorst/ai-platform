import hashlib
import json
import os
import logging
import httpx
import uuid
import re
import socket
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List
from urllib.parse import urlparse

from app.core.security import api_key_auth, require_role
from app.core.database import get_db
from app.models.models import AIConnectedAccount
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")

logger = logging.getLogger(__name__)


# ── Connection Trace Infrastructure ──

CONNECTOR_STAGES = [
    "frontend_submit",
    "ai_core_received",
    "ai_core_verify_payload",
    "connector_received",
    "odoo_client_connect",
    "odoo_rpc_result",
    "key_vault_store",
    "db_save",
]


def _generate_connection_attempt_id() -> str:
    return f"odoo_conn_{uuid.uuid4().hex[:16]}"


def _get_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _key_fingerprint(key: str | None) -> str:
    """Deterministic hash prefix for matching keys without revealing secrets.
    Format: sha256:{first_8_chars}...{last_4_chars}"""
    if not key:
        return ""
    h = hashlib.sha256(key.encode()).hexdigest()
    return f"sha256:{h[:8]}...{h[-4:]}"


def _host_from_url(url: str) -> str:
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


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


class StageTrace(BaseModel):
    odoo_url: Optional[str] = None
    odoo_host: Optional[str] = None
    odoo_db: Optional[str] = None
    odoo_username: Optional[str] = None
    api_key_present: Optional[bool] = None
    api_key_fingerprint: Optional[str] = None
    connector_url: Optional[str] = None
    connector_host: Optional[str] = None
    internal_key_present: Optional[bool] = None
    internal_key_fingerprint: Optional[str] = None
    transport: Optional[str] = None
    model: Optional[str] = None
    method: Optional[str] = None
    domain_summary: Optional[str] = None
    fields: Optional[list] = None
    limit: Optional[int] = None
    status: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    technical_detail: Optional[str] = None
    connection_attempt_id: Optional[str] = None


class ConnectTrace(BaseModel):
    request_id: str = ""
    connection_attempt_id: str = ""
    stages: dict[str, StageTrace] = {}


class ConnectErrorDetail(BaseModel):
    error_type: str = ""
    stage: str = ""
    message: str = ""
    technical_detail: str = ""
    request_id: str = ""
    connection_attempt_id: str = ""
    trace: Optional[dict] = None


@router.get("/debug/connector")
async def debug_connector_connectivity(
    auth: dict = Depends(require_role(["AIPlatform.Admin"])),
):
    """Debug endpoint to test DNS resolution and connectivity to Odoo Connector.
    Requires admin-level authentication."""
    results = {
        "odoo_connector_url": ODOO_CONNECTOR_URL,
        "odoo_connector_key_configured": bool(ODOO_CONNECTOR_KEY),
        "dns_resolution": None,
        "http_connectivity": None,
        "environment_vars": {
            "ODOO_CONNECTOR_URL": ODOO_CONNECTOR_URL,
            "ODOO_CONNECTOR_API_KEY": "***" if ODOO_CONNECTOR_KEY else None,
        }
    }

    if not ODOO_CONNECTOR_URL:
        results["error"] = "ODOO_CONNECTOR_URL is not configured"
        return results

    # Test DNS resolution
    try:
        parsed = urlparse(ODOO_CONNECTOR_URL)
        hostname = parsed.hostname
        if hostname:
            ip_addresses = socket.getaddrinfo(hostname, None)
            results["dns_resolution"] = {
                "hostname": hostname,
                "resolved": True,
                "ip_addresses": list(set([addr[4][0] for addr in ip_addresses]))
            }
        else:
            results["dns_resolution"] = {"error": "Could not parse hostname from URL"}
    except socket.gaierror as e:
        results["dns_resolution"] = {
            "resolved": False,
            "error": f"DNS resolution failed: {str(e)}"
        }
    except Exception as e:
        results["dns_resolution"] = {
            "error": f"Unexpected error during DNS resolution: {str(e)}"
        }

    # Test HTTP connectivity
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            health_url = f"{ODOO_CONNECTOR_URL.rstrip('/')}/health"
            response = await client.get(health_url)
            results["http_connectivity"] = {
                "url": health_url,
                "status_code": response.status_code,
                "reachable": response.status_code == 200,
                "response_time_ms": response.elapsed.total_seconds() * 1000
            }
    except httpx.ConnectError as e:
        results["http_connectivity"] = {
            "reachable": False,
            "error": f"Connection failed: {str(e)}"
        }
    except httpx.TimeoutException as e:
        results["http_connectivity"] = {
            "reachable": False,
            "error": f"Connection timeout: {str(e)}"
        }
    except Exception as e:
        results["http_connectivity"] = {
            "error": f"Unexpected error during HTTP connectivity test: {str(e)}"
        }

    return results


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


class OdooRotateRequest(BaseModel):
    odoo_api_key: str = Field(..., description="New Odoo API key or password")


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
            "domain": [],
            "fields": ["id", "name", "currency_id"],
            "limit": 1,
            "include_ids": True,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ODOO_CONNECTOR_URL.rstrip('/')}/records/search-read",
                json=payload,
                headers=headers,
            )
        if response.status_code >= 400:
            logger.warning("Failed to fetch company metadata from Odoo: %s", response.text)
            return {}

        data = response.json()
        records = data.get("records") if isinstance(data, dict) else data
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
    trace: Optional[ConnectTrace] = None,
) -> None:
    """Uses the Odoo Connector API to perform a safe read-only call to verify credentials.

    Raises HTTPException with structured ConnectErrorDetail on failure.
    Populates trace stages when provided.
    """
    logger.info("Verifying Odoo credentials for user=%s at host=%s db=%s", username, url, db)
    if not ODOO_CONNECTOR_URL:
        if trace and trace.connection_attempt_id:
            trace.stages["ai_core_verify_payload"] = StageTrace(
                status="failed",
                error_type="odoo_connector_url_missing",
                technical_detail="ODOO_CONNECTOR_URL environment variable is not set.",
            )
        raise HTTPException(
            status_code=500,
            detail=ConnectErrorDetail(
                error_type="odoo_connector_url_missing",
                stage="verify_connector",
                message="Odoo Connector is not configured.",
                technical_detail="ODOO_CONNECTOR_URL environment variable is not set.",
            ).model_dump()
        )

    headers = {
        "X-Internal-API-Key": ODOO_CONNECTOR_KEY,
        "Content-Type": "application/json",
    }
    if trace and trace.connection_attempt_id:
        headers["X-Connection-Attempt-Id"] = trace.connection_attempt_id
    payload = {
        "credentials": {
            "url": url,
            "db": db,
            "username": username,
            "api_key": api_key,
            "transport": "auto"
        },
        "model": "res.partner",
        "domain": [],
        "limit": 1
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ODOO_CONNECTOR_URL.rstrip('/')}/records/search-read",
                json=payload,
                headers=headers,
            )
    except httpx.ConnectError as e:
        error_str = str(e)
        is_dns_failure = any(
            phrase in error_str.lower()
            for phrase in [
                "name or service not known",
                "nodename nor servname provided",
                "temporary failure in name resolution",
                "no address associated with hostname",
                "no such host is known",
            ]
        )
        err_type = "odoo_connector_dns_failed" if is_dns_failure else "odoo_connector_unreachable"
        err_msg = (
            "The AI Platform API could not resolve the Odoo Connector service hostname."
            if is_dns_failure
            else "Could not reach the Odoo Connector service. Check network connectivity."
        )
        if trace and trace.connection_attempt_id:
            trace.stages["connector_received"] = StageTrace(status="failed", error_type=err_type, technical_detail=error_str)
        raise HTTPException(
            status_code=502,
            detail=ConnectErrorDetail(
                error_type=err_type,
                stage="verify_connector",
                message=err_msg,
                technical_detail=error_str,
            ).model_dump()
        )
    except httpx.TimeoutException as e:
        if trace and trace.connection_attempt_id:
            trace.stages["connector_received"] = StageTrace(status="failed", error_type="odoo_timeout", technical_detail=str(e))
        raise HTTPException(
            status_code=504,
            detail=ConnectErrorDetail(
                error_type="odoo_timeout",
                stage="verify_connector",
                message="Odoo Connector timed out. Check network connectivity.",
                technical_detail=f"Connection timeout: {e}",
            ).model_dump()
        )
    except httpx.RequestError as e:
        if trace and trace.connection_attempt_id:
            trace.stages["connector_received"] = StageTrace(status="failed", error_type="odoo_connector_unreachable", technical_detail=str(e))
        raise HTTPException(
            status_code=502,
            detail=ConnectErrorDetail(
                error_type="odoo_connector_unreachable",
                stage="verify_connector",
                message="Could not connect to Odoo Connector.",
                technical_detail=f"Request error: {e}",
            ).model_dump()
        )

    if response.status_code >= 400:
        try:
            err_body = response.json()
        except Exception:
            err_body = {"raw": response.text}

        raw_detail = str(err_body.get("message", err_body.get("detail", str(err_body))))
        classified = _classify_odoo_error(raw_detail, response.status_code)

        if response.status_code == 401:
            err_msg = str(err_body.get("detail", err_body))
            if err_body.get("error") == "odoo_auth_failed":
                if trace and trace.connection_attempt_id:
                    trace.stages["odoo_rpc_result"] = StageTrace(
                        status="failed", error_type=classified, technical_detail=err_body.get("message", err_msg),
                    )
                raise HTTPException(
                    status_code=400,
                    detail=ConnectErrorDetail(
                        error_type=classified,
                        stage="verify_odoo",
                        message="Odoo credentials are invalid. Check your URL, database, username, and API key.",
                        technical_detail=f"Odoo auth error: {err_body.get('message', err_msg)}",
                    ).model_dump()
                )
            else:
                if trace and trace.connection_attempt_id:
                    trace.stages["connector_received"] = StageTrace(
                        status="failed", error_type="odoo_connector_auth_failed", technical_detail=f"Connector returned 401: {err_msg}",
                    )
                raise HTTPException(
                    status_code=401,
                    detail=ConnectErrorDetail(
                        error_type="odoo_connector_auth_failed",
                        stage="verify_connector",
                        message="Internal connector API key mismatch. Contact an administrator.",
                        technical_detail=f"Connector returned 401: {err_msg}",
                    ).model_dump()
                )

        if response.status_code == 400 and err_body.get("error") == "odoo_auth_failed":
            if trace and trace.connection_attempt_id:
                trace.stages["odoo_rpc_result"] = StageTrace(
                    status="failed", error_type=classified, technical_detail=err_body.get("message", str(err_body)),
                )
            raise HTTPException(
                status_code=400,
                detail=ConnectErrorDetail(
                    error_type=classified,
                    stage="verify_odoo",
                    message="Odoo credentials are invalid. Check your URL, database, username, and API key.",
                    technical_detail=f"Odoo auth error: {err_body.get('message', str(err_body))}",
                ).model_dump()
            )

        if trace and trace.connection_attempt_id:
            trace.stages["odoo_rpc_result"] = StageTrace(
                status="failed", error_type=classified, technical_detail=raw_detail,
            )
        raise HTTPException(
            status_code=400,
            detail=ConnectErrorDetail(
                error_type=classified,
                stage="verify_odoo",
                message="Odoo verification failed. Check permissions or contact support.",
                technical_detail=raw_detail,
            ).model_dump()
        )


def _store_key_vault_secret(secret_name: str, secret_value: str) -> None:
    """Stores the secret in Azure Key Vault if Key Vault is configured.
    Raises HTTPException on failure, with a user-friendly message for
    ObjectIsDeletedButRecoverable conflicts.
    If KEY_VAULT_URI is not configured, raises so callers know storage failed."""
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if not kv_uri:
        raise HTTPException(
            status_code=500,
            detail=ConnectErrorDetail(
                error_type="key_vault_write_failed",
                stage="store_secret",
                message="Key Vault is not configured. Credentials cannot be stored securely.",
                technical_detail="KEY_VAULT_URI environment variable is not set.",
            ).model_dump()
        )

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=kv_uri, credential=credential)
        kv_client.set_secret(secret_name, secret_value)
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
                    stage="store_secret",
                    message="Could not save connection credentials because a previously "
                           "deleted secret is still reserved. Please retry, or contact "
                           "support if the issue persists.",
                    technical_detail=f"ObjectIsDeletedButRecoverable for secret '{secret_name}'",
                ).model_dump()
            )
        logger.error("Failed to store secret '%s' in Key Vault: %s", secret_name, error_str)
        raise HTTPException(
            status_code=500,
            detail=ConnectErrorDetail(
                error_type="key_vault_write_failed",
                stage="store_secret",
                message="Failed to save connection credentials securely. Please try again.",
                technical_detail=error_str,
            ).model_dump()
        )


def _delete_key_vault_secret(secret_name: str) -> None:
    """Deletes the secret in Azure Key Vault if Key Vault is configured.
    Does not raise if the secret doesn't exist (already deleted or never created)."""
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if not kv_uri:
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=kv_uri, credential=credential)
        # Delete secret
        poller = kv_client.begin_delete_secret(secret_name)
        poller.wait()
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


async def _retrieve_key_vault_secret(secret_name: str) -> str:
    """Retrieves the secret from Azure Key Vault.
    Raises HTTPException if Key Vault is not configured or secret not found."""
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if not kv_uri:
        raise HTTPException(status_code=500, detail="Key Vault is not configured. Cannot retrieve credentials.")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=kv_uri, credential=credential)
        secret = kv_client.get_secret(secret_name)
        return secret.value or ""
    except Exception as e:
        error_str = str(e)
        if "SecretNotFound" in error_str or "NotFound" in error_str:
            raise HTTPException(
                status_code=404,
                detail="Connection credentials not found. Please disconnect and reconnect your Odoo account."
            )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve connection credentials. Please try disconnecting and reconnecting."
        )


def serialize_trace(trace: ConnectTrace, status: str, stage: str, message: str, tech_detail: str) -> dict:
    """Serialize the trace into a dict suitable for API responses and logging.
    Handles optional trace (empty if not populated)."""
    result = {
        "request_id": trace.request_id if trace else "",
        "connection_attempt_id": trace.connection_attempt_id if trace else "",
        "status": status,
        "stage": stage,
        "message": message,
        "technical_detail": tech_detail,
    }
    if trace and trace.stages:
        result["stages"] = {}
        for s, t in trace.stages.items():
            stage_dict = t.model_dump(exclude_none=True) if t else {}
            result["stages"][s] = stage_dict
    return result


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
    connection_attempt_id = _generate_connection_attempt_id()
    trace = ConnectTrace(
        request_id=request_id,
        connection_attempt_id=connection_attempt_id,
        stages={},
    )
    trace.stages["ai_core_received"] = StageTrace(
        odoo_url=req.odoo_url,
        odoo_host=_host_from_url(req.odoo_url),
        odoo_db=req.odoo_db,
        odoo_username=req.odoo_username,
        api_key_present=bool(req.odoo_api_key),
        api_key_fingerprint=_key_fingerprint(req.odoo_api_key),
        connector_url=ODOO_CONNECTOR_URL,
        connector_host=_host_from_url(ODOO_CONNECTOR_URL),
        internal_key_present=bool(ODOO_CONNECTOR_KEY),
        internal_key_fingerprint=_key_fingerprint(ODOO_CONNECTOR_KEY),
        status="received",
    )

    logger.info(
        "Odoo connect request received user_id=%s url=%s db=%s username=%s api_key_present=%s connection_attempt_id=%s",
        user_id,
        req.odoo_url,
        req.odoo_db,
        req.odoo_username,
        bool(req.odoo_api_key),
        connection_attempt_id,
    )

    # Normalize and validate the Odoo URL
    normalized_url = _normalize_odoo_url(req.odoo_url)

    # 1. Resolve account identity before any mutation
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == "odoo",
        )
    )
    existing_account = result.scalar_one_or_none()
    connected_account_id = existing_account.id if existing_account else uuid.uuid4()

    # Use a unique secret name per connection to avoid conflicts with
    # soft-deleted secrets in Key Vault (ObjectIsDeletedButRecoverable).
    secret_name = _generate_secret_name(connected_account_id)

    # 2. Store the Odoo API key in Key Vault FIRST (gate: if this fails, nothing is saved)
    _store_key_vault_secret(secret_name, req.odoo_api_key)
    trace.stages["key_vault_store"] = StageTrace(status="success")

    # 3. Verify credentials (failure does NOT block saving the account)
    verified = False
    company_meta = {}
    verify_error = None
    trace.stages["ai_core_verify_payload"] = StageTrace(
        odoo_url=normalized_url,
        odoo_host=_host_from_url(normalized_url),
        odoo_db=req.odoo_db,
        odoo_username=req.odoo_username,
        api_key_present=bool(req.odoo_api_key),
        api_key_fingerprint=_key_fingerprint(req.odoo_api_key),
        connector_url=ODOO_CONNECTOR_URL,
        connector_host=_host_from_url(ODOO_CONNECTOR_URL),
        internal_key_present=bool(ODOO_CONNECTOR_KEY),
        internal_key_fingerprint=_key_fingerprint(ODOO_CONNECTOR_KEY),
        transport="auto",
        model="res.partner",
        method="search_read",
        domain_summary="[]",
        fields=None,
        limit=1,
        status="pending",
    )
    logger.info(
        "Verifying Odoo via connector url=%s db=%s username=%s connection_attempt_id=%s",
        normalized_url, req.odoo_db, req.odoo_username, connection_attempt_id,
    )
    try:
        await _verify_odoo_credentials_via_connector(
            url=normalized_url,
            db=req.odoo_db,
            username=req.odoo_username,
            api_key=req.odoo_api_key,
            trace=trace,
        )
        verified = True
        trace.stages["odoo_rpc_result"] = StageTrace(status="success")
        # 3b. Fetch company metadata on successful verification
        company_meta = await _fetch_odoo_company_metadata(
            url=normalized_url,
            db=req.odoo_db,
            username=req.odoo_username,
            api_key=req.odoo_api_key,
        )
    except HTTPException as e:
        verify_error = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
    except Exception as e:
        verify_error = {"message": str(e), "technical_detail": str(e)}

    # 4. Save/update the database record with whatever state we have
    now = datetime.utcnow()
    if existing_account:
        existing_account.provider_username = req.odoo_username
        existing_account.secret_reference = secret_name
        existing_account.status = "connected" if verified else "error"
        existing_account.last_verified_at = now if verified else existing_account.last_verified_at
        existing_account.disconnected_at = None
        existing_account.updated_at = now
        existing_account.odoo_url = normalized_url
        existing_account.odoo_db = req.odoo_db
        if company_meta.get("odoo_company_id"):
            existing_account.odoo_company_id = company_meta["odoo_company_id"]
            existing_account.odoo_company_name = company_meta.get("odoo_company_name")
            existing_account.odoo_currency_code = company_meta.get("odoo_currency_code")
            existing_account.odoo_currency_symbol = company_meta.get("odoo_currency_symbol")
        account = existing_account
    else:
        account = AIConnectedAccount(
            id=connected_account_id,
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
        db.add(account)

    await db.commit()
    await db.refresh(account)

    # 5. Log audit event
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="connect",
        target_system="odoo",
        target_model="ai_connected_accounts",
        target_record_id=str(account.id),
        actor_user_id=user_id,
        input_summary=(
            f"Connected Odoo account '{req.odoo_username}' for user {user_id}. "
            f"Verification: {'success' if verified else 'failed'}"
        ),
        risk_level="medium",
        status="success" if verified else "error",
    ))
    await db.commit()

    # 6. If verification failed, return structured error with full trace (account IS saved)
    if not verified:
        err_type = (
            (verify_error or {}).get("error_type")
            or "odoo_credentials_invalid"
        )
        err_stage = (
            (verify_error or {}).get("stage")
            or "verify_odoo"
        )
        err_msg = (
            (verify_error or {}).get("message")
            or "Odoo credentials could not be verified. Your details have been saved with status 'error'."
        )
        tech_detail = (
            (verify_error or {}).get("technical_detail")
            or str(verify_error or "")
        )
        # Build serializable trace for the error response
        trace_dict = serialize_trace(trace, err_type, err_stage, err_msg, tech_detail)
        logger.warning(
            "Odoo connect failed connection_attempt_id=%s user_id=%s error_type=%s stage=%s trace=%s",
            connection_attempt_id, user_id, err_type, err_stage,
            json.dumps(trace_dict, default=str),
        )
        raise HTTPException(
            status_code=400,
            detail=ConnectErrorDetail(
                error_type=err_type,
                stage=err_stage,
                message=err_msg,
                technical_detail=tech_detail,
                request_id=request_id,
                connection_attempt_id=connection_attempt_id,
                trace=trace_dict,
            ).model_dump()
        )

    # 7. On success, log the completed trace
    trace_dict = serialize_trace(trace, "success", "db_save", "Connection saved", "")
    logger.info(
        "Odoo connect succeeded connection_attempt_id=%s user_id=%s trace=%s",
        connection_attempt_id, user_id,
        json.dumps(trace_dict, default=str),
    )

    return account


@router.get("")
async def get_connected_accounts(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Returns normalized connector metadata for all connectors for the authenticated user."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
        )
    )
    db_accounts = result.scalars().all()
    odoo = next((a for a in db_accounts if a.provider == "odoo"), None)

    # Check CLI connector token status
    from app.services.token_storage import token_status as get_token_status
    azure_status = await get_token_status("azure", user_id)
    github_status = await get_token_status("github", user_id)

    connectors = [
        {
            "connector_key": "odoo",
            "display_name": "Odoo Enterprise",
            "status": odoo.status if odoo and odoo.status != "disconnected" else "not_connected",
            "auth_method": "api_key",
            "last_verified_at": odoo.last_verified_at.isoformat() if odoo and odoo.last_verified_at else None,
            "actions_available": ["connect", "test", "disconnect"] if not odoo or odoo.status == "disconnected" else ["test", "disconnect"],
            "metadata": {
                "odoo_url": odoo.odoo_url if odoo else None,
                "odoo_db": odoo.odoo_db if odoo else None,
                "provider_username": odoo.provider_username if odoo else None,
            } if odoo else {},
        },
        {
            "connector_key": "azure_cli",
            "display_name": "Azure CLI",
            "status": azure_status.get("status", "not_connected"),
            "auth_method": "delegated_microsoft",
            "last_verified_at": azure_status.get("expires_on"),
            "actions_available": ["connect", "test", "disconnect"],
        },
        {
            "connector_key": "github_cli",
            "display_name": "GitHub CLI",
            "status": github_status.get("status", "not_connected"),
            "auth_method": "github_oauth",
            "last_verified_at": None,
            "actions_available": ["connect", "test", "disconnect"],
        },
        {
            "connector_key": "ms365",
            "display_name": "Microsoft 365",
            "status": "coming_soon",
            "auth_method": "none",
            "last_verified_at": None,
            "actions_available": [],
        },
    ]
    return {"connectors": connectors}


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


@router.post("/odoo/test", response_model=OdooStatusResponse)
async def test_odoo_connection(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Performs a test of the user's Odoo credentials using Odoo Connector."""
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

    # 1. Use the saved Odoo URL/DB from the connected account record.
    #    Fall back to company facts or env vars for backwards compatibility
    #    with accounts created before odoo_url/odoo_db were added.
    odoo_url = account.odoo_url or ""
    odoo_db = account.odoo_db or ""
    if not odoo_url or not odoo_db:
        from app.models.models import AICompanyFact
        url_fact_res = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_url"))
        db_fact_res = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_primary_db"))
        url_fact = url_fact_res.scalar_one_or_none()
        db_fact = db_fact_res.scalar_one_or_none()
        odoo_url = url_fact.value if url_fact else os.environ.get("ODOO_URL", "")
        odoo_db = db_fact.value if db_fact else os.environ.get("ODOO_DB", "")

    if not odoo_url or not odoo_db:
        raise HTTPException(
            status_code=500,
            detail="Odoo URL or DB name not configured."
        )

    # 2. Retrieve credentials from Key Vault
    api_key = await _retrieve_key_vault_secret(account.secret_reference)

    # 3. Call verification helper
    test_status = "connected"
    try:
        await _verify_odoo_credentials_via_connector(
            url=odoo_url,
            db=odoo_db,
            username=account.provider_username,
            api_key=api_key
        )
        account.status = "connected"
        account.last_verified_at = datetime.utcnow()

        # Refresh company metadata
        company_meta = await _fetch_odoo_company_metadata(
            url=odoo_url,
            db=odoo_db,
            username=account.provider_username,
            api_key=api_key,
        )
        if company_meta.get("odoo_company_id"):
            account.odoo_company_id = company_meta["odoo_company_id"]
            account.odoo_company_name = company_meta.get("odoo_company_name")
            account.odoo_currency_code = company_meta.get("odoo_currency_code")
            account.odoo_currency_symbol = company_meta.get("odoo_currency_symbol")
    except Exception as e:
        test_status = "error"
        account.status = "error"
        # We still update verified/last verified timestamp to reflect test run
        account.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(account)

    # 4. Log audit event
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="test_connection",
        target_system="odoo",
        target_model="ai_connected_accounts",
        target_record_id=str(account.id),
        actor_user_id=user_id,
        input_summary=f"Tested Odoo connection for user {user_id}. Result: {test_status}",
        risk_level="low",
        status="success" if test_status == "connected" else "error",
    ))
    await db.commit()

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


@router.post("/odoo/rotate", response_model=ConnectedAccountResponse)
async def rotate_odoo_credentials(
    req: OdooRotateRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Rotates/updates the Odoo API key/password in Key Vault."""
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
            detail="Odoo connected account not found. Please connect first."
        )

    # Use the saved Odoo URL/DB from the connected account record.
    # Fall back to company facts or env vars for backwards compatibility.
    odoo_url = account.odoo_url or ""
    odoo_db = account.odoo_db or ""
    if not odoo_url or not odoo_db:
        from app.models.models import AICompanyFact
        url_fact_res = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_url"))
        db_fact_res = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_primary_db"))
        url_fact = url_fact_res.scalar_one_or_none()
        db_fact = db_fact_res.scalar_one_or_none()
        odoo_url = url_fact.value if url_fact else os.environ.get("ODOO_URL", "")
        odoo_db = db_fact.value if db_fact else os.environ.get("ODOO_DB", "")

    # Validate the new credentials
    await _verify_odoo_credentials_via_connector(
        url=odoo_url,
        db=odoo_db,
        username=account.provider_username,
        api_key=req.odoo_api_key
    )

    # Generate a new unique secret name for the rotated key
    new_secret_name = _generate_secret_name(account.id)
    _store_key_vault_secret(new_secret_name, req.odoo_api_key)

    # Update metadata and point to the new secret
    account.secret_reference = new_secret_name
    account.status = "connected"
    account.last_verified_at = datetime.utcnow()
    account.updated_at = datetime.utcnow()
    account.disconnected_at = None

    await db.commit()
    await db.refresh(account)

    # Log audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="rotate_credentials",
        target_system="odoo",
        target_model="ai_connected_accounts",
        target_record_id=str(account.id),
        actor_user_id=user_id,
        input_summary=f"Rotated Odoo credentials for user {user_id}",
        risk_level="medium",
        status="success",
    ))
    await db.commit()

    return account


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
        _delete_key_vault_secret(account.secret_reference)

    # 2. Clear all connection metadata and credentials
    account.status = "disconnected"
    account.secret_reference = None
    account.provider_username = None
    account.provider_user_id = None
    account.provider_display_name = None
    account.permission_summary = None
    account.last_verified_at = None
    account.disconnected_at = datetime.utcnow()
    account.updated_at = datetime.utcnow()
    account.odoo_url = None
    account.odoo_db = None
    account.odoo_company_id = None
    account.odoo_company_name = None
    account.odoo_currency_code = None
    account.odoo_currency_symbol = None

    await db.commit()
    await db.refresh(account)

    # 3. Log audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="disconnect",
        target_system="odoo",
        target_model="ai_connected_accounts",
        target_record_id=str(account.id),
        actor_user_id=user_id,
        input_summary=f"Disconnected Odoo account for user {user_id}",
        risk_level="medium",
        status="success",
    ))
    await db.commit()

    return account
