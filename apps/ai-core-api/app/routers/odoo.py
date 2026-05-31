import os
import logging
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
from app.services.job import JobService
from app.services.artifact import ArtifactService
from app.schemas.schemas import AIAuditEventCreate, AIJobCreate, AIArtifactCreate

logger = logging.getLogger(__name__)

router = APIRouter()

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")

# Methods that require explicit policy approval at AI Core level
EXECUTE_KW_DANGEROUS_METHODS = {"unlink", "sudo", "with_context", "env", "__import__"}
# Write methods that need explicit allow
EXECUTE_KW_WRITE_METHODS = {"create", "write", "copy", "message_post"}


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
    identity_mode: str = "user-delegated"
    create_job: bool = False
    job_title: Optional[str] = None


def _get_connector_headers():
    return {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}


def _get_connector_url(path: str) -> str:
    base = ODOO_CONNECTOR_URL.rstrip("/")
    return f"{base}{path}"


async def _log_audit(
    db: AsyncSession,
    auth: dict,
    action_type: str,
    target_model: str,
    status: str,
    details: dict,
    identity_mode: str = "user-delegated",
) -> None:
    try:
        audit_svc = AuditService(db)
        await audit_svc.log_event(AIAuditEventCreate(
            action_type=action_type,
            target_system="odoo",
            target_model=target_model,
            target_record_id=None,
            actor_user_id=auth.get("user_id"),
            identity_mode=identity_mode,
            input_summary=str(details)[:500],
            status=status,
        ))
        await db.commit()
    except Exception:
        await db.rollback()


async def _resolve_odoo_credentials(db: AsyncSession, user_id: UUID, identity_mode: str = "user-delegated") -> dict[str, str]:
    # Service account mode: use configured service account credentials
    if identity_mode == "service-account":
        service_url = os.environ.get("ODOO_SERVICE_URL", "")
        service_db = os.environ.get("ODOO_SERVICE_DB", "")
        service_username = os.environ.get("ODOO_SERVICE_USERNAME", "")
        service_api_key = os.environ.get("ODOO_SERVICE_API_KEY", "")
        
        if not all([service_url, service_db, service_username, service_api_key]):
            raise HTTPException(
                status_code=500,
                detail="Service account credentials not configured. Set ODOO_SERVICE_* environment variables.",
            )
        
        return {
            "url": service_url,
            "db": service_db,
            "username": service_username,
            "api_key": service_api_key,
            "transport": "auto",
        }
    
    # User-delegated mode: resolve from connected account
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
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

    # Use the saved Odoo URL/DB from the connected account record.
    # Fall back to company facts or env vars for backwards compatibility.
    odoo_url = account.odoo_url or ""
    odoo_db = account.odoo_db or ""
    if not odoo_url or not odoo_db:
        url_fact_res = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_url"))
        db_fact_res = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_primary_db"))
        url_fact = url_fact_res.scalar_one_or_none()
        db_fact = db_fact_res.scalar_one_or_none()
        odoo_url = url_fact.value if url_fact else os.environ.get("ODOO_URL", "")
        odoo_db = db_fact.value if db_fact else os.environ.get("ODOO_DB", "")

    if not odoo_url or not odoo_db:
        raise HTTPException(
            status_code=500,
            detail="Odoo URL or database not configured.",
        )

    logger.info("Resolved Odoo credentials for user=%s host=%s db=%s", account.provider_username, odoo_url, odoo_db)

    return {
        "url": odoo_url,
        "db": odoo_db,
        "username": account.provider_username or "",
        "api_key": api_key,
        "transport": "auto",
    }


async def _create_odoo_job(
    db: AsyncSession,
    auth: dict,
    req: OdooToolRequest,
    result: dict,
) -> dict:
    try:
        job_svc = JobService(db)
        job = await job_svc.create(
            AIJobCreate(
                workflow_type="odoo",
                title=req.job_title or f"Odoo {req.model}",
                linked_system="odoo",
                linked_model=req.model,
                linked_record_id=str(req.record_id) if req.record_id else None,
            ),
            requested_by_user_id=auth.get("user_id"),
        )

        artifact_svc = ArtifactService(db)
        artifact = await artifact_svc.upload_json(
            AIArtifactCreate(
                job_id=job.id,
                artifact_type="odoo-result",
                filename=f"odoo-{req.model or 'result'}.json",
                mime_type="application/json",
                source_tool="odoo",
                stage="final",
            ),
            result,
            created_by_user_id=auth.get("user_id"),
        )

        await job_svc.update_status(job.id, "completed", summary=f"Artifact {artifact.id} created")
        await db.commit()
        return {"job_id": str(job.id), "artifact_id": str(artifact.id)}
    except Exception:
        await db.rollback()
        return {}


async def _check_policy(db: AsyncSession, action: str, details: dict[str, Any]) -> None:
    result = await db.execute(
        select(AIRule).where(
            AIRule.status == "active",
            AIRule.scope_type.in_(["global", "odoo"]),
        )
    )
    rules = result.scalars().all()

    for rule in rules:
        body_lower = (rule.body or "").lower()

        if action == "message_create" and "chatter" in body_lower:
            if "raw" in body_lower or "csv" in body_lower or "json" in body_lower or "table" in body_lower:
                body_text = details.get("body", "")
                if len(body_text) > 1000 or any(x in body_text.lower() for x in ["csv", "json", "table", "|"]):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Policy blocked: {rule.title}. Chatter messages must be short summaries. Raw data dumps are not allowed.",
                    )

        if action == "attachment_create" and "artifact" in body_lower:
            filename = details.get("filename", "").lower()
            if any(x in filename for x in [".csv", ".json", ".debug", ".tmp", ".ocr", ".parsed"]):
                raise HTTPException(
                    status_code=403,
                    detail=f"Policy blocked: {rule.title}. Intermediate/debug files must be stored in AI Platform Blob storage, not attached to Odoo.",
                )

        if action == "execute" and "execute_kw" in body_lower:
            method = details.get("method", "")
            if method == "unlink":
                raise HTTPException(
                    status_code=403,
                    detail=f"Policy blocked: {rule.title}. Delete operations (unlink) require explicit policy approval.",
                )


def _check_execute_kw_method(method: str, allow_write: bool = False) -> None:
    """Gate execute_kw methods at AI Core level. Dangerous methods always blocked. Write methods require explicit allow."""
    if method in EXECUTE_KW_DANGEROUS_METHODS:
        raise HTTPException(
            status_code=403,
            detail=f"Method '{method}' is blocked. Dangerous/destructive methods require explicit policy approval.",
        )
    if method in EXECUTE_KW_WRITE_METHODS and not allow_write:
        raise HTTPException(
            status_code=403,
            detail=f"Write method '{method}' is blocked. Set operation_mode='write-allowed' to enable.",
        )


async def _call_connector(
    method: str,
    path: str,
    payload: dict,
) -> dict[str, Any]:
    url = _get_connector_url(path)
    headers = _get_connector_headers()

    async with httpx.AsyncClient(timeout=120.0) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        else:
            response = await client.post(url, json=payload, headers=headers)

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
    try:
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "query": req.model or "",
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        if req.model:
            payload["model"] = req.model
            payload["fields"] = req.fields
            result = await _call_connector("POST", "/schema/fields", payload)
        else:
            result = await _call_connector("POST", "/schema/models", payload)
        await _log_audit(db, auth, "odoo_proxy", req.model or "ir.model", "success", 
                        {"path": "/schema", "model": req.model}, identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", req.model or "ir.model", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/search-read")
async def odoo_search_read(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "model": req.model,
            "domain": req.domain,
            "fields": req.fields,
            "limit": req.limit,
            "offset": req.offset,
            "order": req.order,
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/records/search-read", payload)
        await _log_audit(db, auth, "odoo_proxy", req.model or "", "success", 
                        {"path": "/records/search-read", "model": req.model}, identity_mode=req.identity_mode)
        if req.create_job:
            job_meta = await _create_odoo_job(db, auth, req, result)
            result["_job"] = job_meta
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", req.model or "", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/execute")
async def odoo_execute(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        # Gate execute_kw methods at AI Core level
        allow_write = req.operation_mode == "write-allowed"
        _check_execute_kw_method(req.method or "", allow_write=allow_write)
        
        await _check_policy(db, "execute", {"method": req.method, "model": req.model})
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "model": req.model,
            "method": req.method,
            "args": req.args,
            "kwargs": req.kwargs,
            "dry_run": req.dry_run,
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/execute-kw/", payload)
        await _log_audit(db, auth, "odoo_proxy", req.model or "", "success", 
                        {"path": "/execute-kw", "model": req.model, "method": req.method},
                        identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", req.model or "", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/attachments/list")
async def odoo_attachments_list(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "model": req.model,
            "record_id": req.record_id,
            "limit": req.limit,
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/attachments/list", payload)
        await _log_audit(db, auth, "odoo_proxy", "ir.attachment", "success", 
                        {"path": "/attachments/list"}, identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", "ir.attachment", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/attachments/get")
async def odoo_attachments_get(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "attachment_id": req.attachment_id,
            "mode": "metadata",
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/attachments/get", payload)
        await _log_audit(db, auth, "odoo_proxy", "ir.attachment", "success", 
                        {"path": "/attachments/get"}, identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", "ir.attachment", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/attachments/create")
async def odoo_attachments_create(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        await _check_policy(db, "attachment_create", {"filename": req.filename or ""})
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "model": req.model,
            "record_id": req.record_id,
            "filename": req.filename,
            "content_base64": req.content_base64,
            "mimetype": req.mimetype,
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/attachments/create", payload)
        await _log_audit(db, auth, "odoo_proxy", "ir.attachment", "success", 
                        {"path": "/attachments/create"}, identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", "ir.attachment", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/messages/list")
async def odoo_messages_list(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "model": req.model,
            "record_id": req.record_id,
            "limit": req.limit,
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/messages/list", payload)
        await _log_audit(db, auth, "odoo_proxy", "mail.message", "success", 
                        {"path": "/messages/list"}, identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", "mail.message", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise


@router.post("/messages/create")
async def odoo_messages_create(
    req: OdooToolRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        await _check_policy(db, "message_create", {"body": req.body or ""})
        credentials = await _resolve_odoo_credentials(db, auth["user_id"], req.identity_mode)
        payload = {
            "credentials": credentials,
            "model": req.model,
            "record_id": req.record_id,
            "body": req.body,
            "target_environment": req.target_environment,
            "operation_mode": req.operation_mode,
            "identity_mode": req.identity_mode,
        }
        result = await _call_connector("POST", "/messages/create", payload)
        await _log_audit(db, auth, "odoo_proxy", "mail.message", "success", 
                        {"path": "/messages/create"}, identity_mode=req.identity_mode)
        return result
    except HTTPException as e:
        await _log_audit(db, auth, "odoo_proxy", "mail.message", "blocked" if e.status_code == 403 else "error", 
                        {"error": e.detail}, identity_mode=req.identity_mode)
        raise
