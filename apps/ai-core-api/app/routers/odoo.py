import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Optional
from app.core.config import get_settings
from app.core.security import api_key_auth
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")


class OdooCredentialsPayload(BaseModel):
    url: str
    db: str
    username: str
    api_key: str
    transport: str = "auto"


class OdooSchemaRequest(BaseModel):
    credentials: OdooCredentialsPayload
    query: Optional[str] = None
    model: Optional[str] = None
    fields: Optional[list[str]] = None


class OdooSearchReadRequest(BaseModel):
    credentials: OdooCredentialsPayload
    model: str
    domain: Optional[list[Any]] = None
    fields: Optional[list[str]] = None
    limit: int = 50


class OdooExecuteRequest(BaseModel):
    credentials: OdooCredentialsPayload
    model: str
    method: str
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None


class OdooAttachmentListRequest(BaseModel):
    credentials: OdooCredentialsPayload
    model: Optional[str] = None
    record_id: Optional[int] = None
    limit: int = 50


class OdooAttachmentGetRequest(BaseModel):
    credentials: OdooCredentialsPayload
    attachment_id: int


class OdooAttachmentCreateRequest(BaseModel):
    credentials: OdooCredentialsPayload
    model: str
    record_id: int
    filename: str
    content_base64: str
    mimetype: Optional[str] = None


class OdooMessageListRequest(BaseModel):
    credentials: OdooCredentialsPayload
    model: Optional[str] = None
    record_id: Optional[int] = None
    limit: int = 50


class OdooMessageCreateRequest(BaseModel):
    credentials: OdooCredentialsPayload
    model: str
    record_id: int
    body: str


def _get_connector_headers():
    return {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}


def _get_connector_url(path: str) -> str:
    base = ODOO_CONNECTOR_URL.rstrip("/")
    return f"{base}{path}"


async def _call_connector(method: str, path: str, payload: dict, db: AsyncSession, auth: dict):
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
        details={"path": path, "method": method, "status": response.status_code},
    ))

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return response.json()


@router.post("/schema")
async def odoo_schema(
    req: OdooSchemaRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    if req.model:
        return await _call_connector("POST", "/schema/fields", req.model_dump(), db, auth)
    return await _call_connector("POST", "/schema/models", req.model_dump(), db, auth)


@router.post("/search-read")
async def odoo_search_read(
    req: OdooSearchReadRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/records/search-read", req.model_dump(), db, auth)


@router.post("/execute")
async def odoo_execute(
    req: OdooExecuteRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/execute-kw/", req.model_dump(), db, auth)


@router.post("/attachments/list")
async def odoo_attachments_list(
    req: OdooAttachmentListRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/attachments/list", req.model_dump(), db, auth)


@router.post("/attachments/get")
async def odoo_attachments_get(
    req: OdooAttachmentGetRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/attachments/get", req.model_dump(), db, auth)


@router.post("/attachments/create")
async def odoo_attachments_create(
    req: OdooAttachmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/attachments/create", req.model_dump(), db, auth)


@router.post("/messages/list")
async def odoo_messages_list(
    req: OdooMessageListRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/messages/list", req.model_dump(), db, auth)


@router.post("/messages/create")
async def odoo_messages_create(
    req: OdooMessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    return await _call_connector("POST", "/messages/create", req.model_dump(), db, auth)
