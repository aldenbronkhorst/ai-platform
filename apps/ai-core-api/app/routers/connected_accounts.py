import os
import logging
import httpx
import uuid
import re
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List

from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import AIConnectedAccount
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")

logger = logging.getLogger(__name__)


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
            "identity_mode": "user-delegated",
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
                f"{ODOO_CONNECTOR_URL.rstrip('/')}/execute-kw/",
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
            if isinstance(currency_data, list) and len(currency_data) >= 2:
                currency_code = str(currency_data[1]) if currency_data[1] else None
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


async def _verify_odoo_credentials_via_connector(url: str, db: str, username: str, api_key: str) -> None:
    """Uses the Odoo Connector API to perform a safe read-only call to verify credentials."""
    logger.info("Verifying Odoo credentials for user=%s at host=%s db=%s", username, url, db)
    if not ODOO_CONNECTOR_URL:
        raise HTTPException(
            status_code=500,
            detail="Odoo Connector URL is not configured."
        )

    headers = {
        "X-Internal-API-Key": ODOO_CONNECTOR_KEY,
        "Content-Type": "application/json"
    }
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
        if response.status_code >= 400:
            try:
                err_detail = response.json()
            except Exception:
                err_detail = response.text
            raise HTTPException(
                status_code=400,
                detail=f"Odoo verification failed: {err_detail}"
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to Odoo Connector: {e}"
        )


def _store_key_vault_secret(secret_name: str, secret_value: str) -> None:
    """Stores the secret in Azure Key Vault if Key Vault is configured.
    Raises HTTPException on failure, with a user-friendly message for
    ObjectIsDeletedButRecoverable conflicts."""
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if not kv_uri:
        return

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
                detail="Could not save connection credentials because a previously "
                       "deleted secret is still reserved. Please retry, or contact "
                       "support if the issue persists."
            )
        logger.error("Failed to store secret '%s' in Key Vault: %s", secret_name, error_str)
        raise HTTPException(
            status_code=500,
            detail="Failed to save connection credentials securely. Please try again."
        )


def _delete_key_vault_secret(secret_name: str) -> None:
    """Deletes the secret in Azure Key Vault if Key Vault is configured."""
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
        # Log or raise but let DB transaction proceed. For safety we raise.
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete secret in Key Vault: {e}"
        )


async def _retrieve_key_vault_secret(secret_name: str) -> str:
    """Retrieves the secret from Azure Key Vault."""
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if not kv_uri:
        # Safe fallback for local/debug env if KEY_VAULT_URI is missing
        return "mock-local-secret"

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=kv_uri, credential=credential)
        secret = kv_client.get_secret(secret_name)
        return secret.value or ""
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve secret from Key Vault: {e}"
        )


@router.post("/odoo/connect", response_model=ConnectedAccountResponse)
async def connect_odoo(
    req: OdooConnectRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Saves/creates Odoo connection. Validates credentials first, then saves API key to Key Vault."""
    user_id = auth.get("user_id")

    # Normalize and validate the Odoo URL
    normalized_url = _normalize_odoo_url(req.odoo_url)

    # 1. Validate Odoo credentials using a safe read-only call
    await _verify_odoo_credentials_via_connector(
        url=normalized_url,
        db=req.odoo_db,
        username=req.odoo_username,
        api_key=req.odoo_api_key
    )

    # 1b. Fetch company metadata (currency, company name) from Odoo
    company_meta = await _fetch_odoo_company_metadata(
        url=normalized_url,
        db=req.odoo_db,
        username=req.odoo_username,
        api_key=req.odoo_api_key,
    )

    # 2. Check if a connection already exists
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id),
            AIConnectedAccount.provider == "odoo",
        )
    )
    account = result.scalar_one_or_none()

    if account:
        connected_account_id = account.id
    else:
        connected_account_id = uuid.uuid4()

    # Use a unique secret name per connection to avoid conflicts with
    # soft-deleted secrets in Key Vault (ObjectIsDeletedButRecoverable).
    secret_name = _generate_secret_name(connected_account_id)

    # 3. Store the Odoo API key in Key Vault under opaque secret reference
    _store_key_vault_secret(secret_name, req.odoo_api_key)

    # 4. Create/update the database record, saving the user-provided URL and DB
    if account:
        account.provider_username = req.odoo_username
        account.secret_reference = secret_name
        account.status = "connected"
        account.last_verified_at = datetime.utcnow()
        account.disconnected_at = None
        account.updated_at = datetime.utcnow()
        account.odoo_url = normalized_url
        account.odoo_db = req.odoo_db
        if company_meta.get("odoo_company_id"):
            account.odoo_company_id = company_meta["odoo_company_id"]
            account.odoo_company_name = company_meta.get("odoo_company_name")
            account.odoo_currency_code = company_meta.get("odoo_currency_code")
            account.odoo_currency_symbol = company_meta.get("odoo_currency_symbol")
    else:
        account = AIConnectedAccount(
            id=connected_account_id,
            user_id=user_id,
            provider="odoo",
            provider_username=req.odoo_username,
            secret_reference=secret_name,
            status="connected",
            last_verified_at=datetime.utcnow(),
            target_environment="production",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
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
        input_summary=f"Connected Odoo account '{req.odoo_username}' for user {user_id}",
        risk_level="medium",
        status="success",
    ))
    await db.commit()

    return account


@router.get("", response_model=List[ConnectedAccountResponse])
async def get_connected_accounts(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Returns list of all connected accounts for the authenticated user."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id)
        )
    )
    return result.scalars().all()


@router.get("/odoo/status", response_model=OdooStatusResponse)
async def get_odoo_status(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Returns status of the Odoo connection for the authenticated user."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id),
            AIConnectedAccount.provider == "odoo",
        )
    )
    account = result.scalar_one_or_none()

    if not account:
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


@router.post("/odoo/test", response_model=OdooStatusResponse)
async def test_odoo_connection(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Performs a test of the user's Odoo credentials using Odoo Connector."""
    user_id = auth.get("user_id")
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id),
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
            AIConnectedAccount.user_id == str(user_id),
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
            AIConnectedAccount.user_id == str(user_id),
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

    # 2. Update DB metadata
    account.status = "disconnected"
    account.disconnected_at = datetime.utcnow()
    account.updated_at = datetime.utcnow()

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
