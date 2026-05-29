import os
import httpx
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Any, Optional

from app.core.config import get_settings
from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import AIConnectedAccount, AIRule, AICompanyFact
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

router = APIRouter()

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")


class OdooToolRequest(BaseModel):
    model: Optional[str] = None
    domain: Optional[list[Any]] = None
    fields: Optional[list[str]] = None
    limit: int = 50
    offset: int = 0
    order: Optional[str] = None
    ids: Optional[list[int]] = None
    values: Optional[dict[str, Any]] = None
    method: Optional[str] = None
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None
    attachment_id: Optional[int] = None
    record_id: Optional[int] = None
    filename: Optional[str] = None
    content_base64: Optional[str] = None
    mimetype: Optional[str] = None
    body: Optional[str] = None
    workflow_method: Optional[str] = None
    dry_run: bool = False
    target_environment: Optional[str] = None
    operation_mode: Optional[str] = None


def _get_connector_headers():
    return {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}


def _get_connector_url(path: str) -> str:
    base = ODOO_CONNECTOR_URL.rstrip("/")
    return f"{base}{path}"


async def _resolve_odoo_credentials(db: AsyncSession, user_id: UUID) -> dict[str, str]:
    """Resolve the user's Odoo connected account and retrieve credentials from Key Vault."""
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id),
            AIConnectedAccount.provider == "odoo",
            AIConnectedAccount.status == "active",
        )
    )
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(
            status_code=403,
            detail="No Odoo connected account found. Please connect your Odoo account first.",
        )

    # Retrieve the actual API key from Key Vault using the secret_reference
    api_key = ""
    if account.secret_reference and os.environ.get("KEY_VAULT_URI"):
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=os.environ["KEY_VAULT_URI"], credential=credential)
            secret = client.get_secret(account.secret_reference)
            api_key = secret.value or ""
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve Odoo credentials from Key Vault: {e}",
            )

    if not api_key:
        raise HTTPException(
            status_code=403,
            detail="Odoo connected account has no valid credentials. Please reconnect.",
        )

    # Get Odoo URL and DB from company facts
    url_result = await db.execute(
        select(AICompanyFact).where(AICompanyFact.key == "odoo_url")
    )
    db_result = await db.execute(
        select(AICompanyFact).where(AICompanyFact.key == "odoo_primary_db")
    )
    url_fact = url_result.scalar_one_or_none()
    db_fact = db_result.scalar_one_or_none()

    odoo_url = url_fact.value if url_fact else os.environ.get("ODOO_URL", "")
    odoo_db = db_fact.value if db_fact else os.environ.get("ODOO_DB", "")

    if not odoo_url or not odoo_db:
        raise HTTPException(
            status_code=500,
            detail="Odoo URL or database not configured in company facts.",
        )

    return {
        "url": odoo_url,
        "db": odoo_db,
        "username": account.provider_username or "",
        "api_key": api_key,
        "transport": "auto",
    }


async def _check_policy(db: AsyncSession, action: str, details: dict[str, Any]) -> None:
    """Check policy rules before allowing Odoo operations."""
    result = await db.execute(
        select(AIRule).where(
            AIRule.status == "active",
            AIRule.scope_type.in_(["global", "odoo"]),
        )
    )
    rules = result.scalars().all()

    for rule in rules:
        body_lower = (rule.body or "").lower()

        # Block raw chatter dumps
        if action == "message_create" and "chatter" in body_lower:
            if "raw" in body_lower or "csv" in body_lower or "json" in body_lower or "table" in body_lower:
                body_text = details.get("body", "")
                if len(body_text) > 1000 or any(x in body_text.lower() for x in ["csv", "json", "table", "|"]):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Policy blocked: {rule.title}. Chatter messages must be short summaries. Raw data dumps are not allowed.",
                    )

        # Block intermediate artifacts attached to Odoo
        if action == "attachment_create" and "artifact" in body_lower:
            filename = details.get("filename", "").lower()
            if any(x in filename for x in [".csv", ".json", ".debug", ".tmp", ".ocr", ".parsed"]):
                raise HTTPException(
                    status_code=403,
                    detail=f"Policy blocked: {rule.title}. Intermediate/debug files must be stored in AI Platform Blob storage, not attached to Odoo.",
                )

        # Gate dangerous operations
        if action == "execute" and "execute_kw" in body_lower:
            method = details.get("method", "")
            if method == "unlink":
                raise HTTPException(
                    status_code=403,
                    detail=f"Policy blocked: {rule.title}. Delete operations (unlink) require explicit policy approval.",
                )


async def _call_connector(
    method: str,
    path: str,
    payload: dict,
    db: AsyncSession,
    auth: dict,
) -> dict[str, Any]:
    """Call the Odoo Connector API with the given payload."""
    url = _get_connector_url(path)
    headers = _get_connector_headers()

    async with httpx.AsyncClient(timeout=120.0) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        else:
            response = await client.post(url, json=payload, headers=headers)

    # Audit log
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="odoo_proxy",
        target_system="odoo",
        target_model=payload.get("model", "unknown"),
        target_record_id=None,
        actor_user_id=auth.get("user_id"),
        details={
            "path": path,
            "method": method,
            "status": response.status_code,
            "model": payload.get("model"),
        },
    ))

    if response.status_code >= 400:
        try:
            error_detail = response.json()
        except Exception:
            error_detail = response.text
        raise HTTPException(status_code=response.status_code, detail=str(error_detail))

    return response.json()


@router.post("/schema")
async def odoo_schema(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {"credentials": credentials, "query": req.model or ""}
    if req.model:
        payload["model"] = req.model
        payload["fields"] = req.fields
        return await _call_connector("POST", "/schema/fields", payload, db, auth)
    return await _call_connector("POST", "/schema/models", payload, db, auth)


@router.post("/search-read")
async def odoo_search_read(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "model": req.model,
        "domain": req.domain,
        "fields": req.fields,
        "limit": req.limit,
        "offset": req.offset,
        "order": req.order,
    }
    return await _call_connector("POST", "/records/search-read", payload, db, auth)


@router.post("/execute")
async def odoo_execute(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    await _check_policy(db, "execute", {"method": req.method, "model": req.model})
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "model": req.model,
        "method": req.method,
        "args": req.args,
        "kwargs": req.kwargs,
        "dry_run": req.dry_run,
    }
    return await _call_connector("POST", "/execute-kw/", payload, db, auth)


@router.post("/attachments/list")
async def odoo_attachments_list(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "model": req.model,
        "record_id": req.record_id,
        "limit": req.limit,
    }
    return await _call_connector("POST", "/attachments/list", payload, db, auth)


@router.post("/attachments/get")
async def odoo_attachments_get(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "attachment_id": req.attachment_id,
        "mode": "metadata",
    }
    return await _call_connector("POST", "/attachments/get", payload, db, auth)


@router.post("/attachments/create")
async def odoo_attachments_create(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    await _check_policy(db, "attachment_create", {"filename": req.filename or ""})
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "model": req.model,
        "record_id": req.record_id,
        "filename": req.filename,
        "content_base64": req.content_base64,
        "mimetype": req.mimetype,
    }
    return await _call_connector("POST", "/attachments/create", payload, db, auth)


@router.post("/messages/list")
async def odoo_messages_list(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "model": req.model,
        "record_id": req.record_id,
        "limit": req.limit,
    }
    return await _call_connector("POST", "/messages/list", payload, db, auth)


@router.post("/messages/create")
async def odoo_messages_create(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    await _check_policy(db, "message_create", {"body": req.body or ""})
    credentials = await _resolve_odoo_credentials(db, auth["user_id"])
    payload = {
        "credentials": credentials,
        "model": req.model,
        "record_id": req.record_id,
        "body": req.body,
    }
    return await _call_connector("POST", "/messages/create", payload, db, auth)
