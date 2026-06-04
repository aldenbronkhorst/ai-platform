"""Odoo operations runner — consolidated command center for all Odoo operations."""
import html
import logging
import re
from pydantic import BaseModel, Field
from typing import Any, Optional, Callable
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import OdooCredentialsRequest, OdooExecuteReportRequest
from app.services.odoo_report_service import OdooReportService

router = APIRouter()
logger = logging.getLogger(__name__)


class OdooOpsRunnerRequest(BaseModel):
    credentials: OdooCredentialsRequest
    mode: str = Field(..., description="Operation mode")
    model: Optional[str] = None
    domain: Optional[list[Any]] = None
    fields: Optional[list[str]] = None
    ids: Optional[list[int]] = None
    limit: int = 50
    offset: int = 0
    order: Optional[str] = None
    include_ids: bool = True
    report_name: Optional[str] = None
    report_id: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    company_id: Optional[int] = None
    timezone: Optional[str] = None
    lang: Optional[str] = None
    line_names: Optional[list[str]] = None
    include_raw_lines: bool = False
    attachment_id: Optional[int] = None
    attachment_ids: Optional[list[int]] = None
    purpose: Optional[str] = None
    content_fields: Optional[list[str]] = None
    max_content_chars: int = 5000
    operation: Optional[str] = None
    values: Optional[dict[str, Any]] = None
    workflow_method: Optional[str] = None
    target_type: Optional[str] = None
    record_id: Optional[int] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    message_type: Optional[str] = None
    subtype_xmlid: Optional[str] = None
    partner_ids: Optional[list[int]] = None
    attachment_ids_for_message: Optional[list[int]] = None
    body: Optional[str] = None
    query: Optional[str] = None
    raw_html: bool = False
    transport: Optional[str] = None
    method: Optional[str] = None
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url, db=creds.db, username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


def _run_health(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    try:
        uid = client.authenticate()
        return {"status": "healthy", "authenticated": True, "user_id": uid, "database": req.credentials.db}
    except Exception as exc:
        return {"status": "error", "authenticated": False, "error": str(exc)}


def _run_schema(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    if req.query:
        models = client.call_with_transport(
            "ir.model",
            "search_read",
            args=[[["model", "ilike", req.query]], ["model", "name"]],
            kwargs={"limit": req.limit},
        ) or []
        return {"models": models}
    if req.model:
        return {"model": req.model, "fields": client.fields_get(req.model, fields=req.fields)}
    return {"warning": "Provide model or query for schema inspection."}


def _invalid_field_error(exc: Exception) -> bool:
    return "Invalid field" in str(exc)


def _valid_query_fields(client: OdooClient, model: str, requested_fields: list[str]) -> tuple[list[str], list[str], dict[str, Any]]:
    schema = client.fields_get(model, fields=requested_fields)
    available_fields = set((schema.get("fields") or {}).keys())
    valid_fields = [field for field in requested_fields if field == "id" or field in available_fields]
    invalid_fields = [field for field in requested_fields if field not in valid_fields]
    return valid_fields, invalid_fields, schema


def _query_records(client: OdooClient, req: OdooOpsRunnerRequest, fields: list[str] | None = None) -> list[dict[str, Any]]:
    if req.ids:
        return client.read(model=req.model, ids=req.ids, fields=fields)
    return client.search_read(
        model=req.model,
        domain=req.domain or [],
        fields=fields,
        limit=req.limit,
        offset=req.offset,
        order=req.order,
        include_ids=req.include_ids,
    )


def _run_query(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    try:
        records = _query_records(client, req, req.fields)
        return {"model": req.model, "records": records, "count": len(records)}
    except Exception as exc:
        if not req.fields or not req.model or not _invalid_field_error(exc):
            raise

    valid_fields, invalid_fields, schema = _valid_query_fields(client, req.model, req.fields)
    if not valid_fields:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_fields",
            "message": "None of the requested fields exist on this Odoo model.",
            "model": req.model,
            "invalid_fields": invalid_fields,
            "field_errors": schema.get("field_errors"),
        })

    records = _query_records(client, req, valid_fields)
    return {
        "model": req.model,
        "records": records,
        "count": len(records),
        "warning": "Some requested fields do not exist on this Odoo model and were omitted.",
        "omitted_invalid_fields": invalid_fields,
        "field_errors": schema.get("field_errors"),
    }


def _run_count(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    return {"model": req.model, "count": client.search_count(model=req.model, domain=req.domain or [])}


def _run_aggregate(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    if not req.model or not req.fields or not req.args:
        raise HTTPException(status_code=400, detail={"error": "aggregate requires model, fields, and groupby"})
    result = client.call_with_transport(
        req.model,
        "read_group",
        args=[req.domain or [], req.fields, req.args],
        kwargs={"lazy": True},
    )
    return {"model": req.model, "groups": result}


def _run_report(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    report_req = OdooExecuteReportRequest(
        credentials=req.credentials,
        report_name=req.report_name or "",
        report_id=req.report_id,
        date_from=req.date_from,
        date_to=req.date_to,
        company_id=req.company_id,
        timezone=req.timezone,
        lang=req.lang,
        line_names=req.line_names,
        include_raw_lines=req.include_raw_lines,
    )
    return OdooReportService(client).execute(report_req)


def _requested_attachment_ids(req: OdooOpsRunnerRequest) -> list[int]:
    attachment_ids = []
    if req.attachment_id:
        attachment_ids.append(req.attachment_id)
    if req.attachment_ids:
        attachment_ids.extend(req.attachment_ids)
    if not attachment_ids:
        raise HTTPException(status_code=400, detail={"error": "attachment_id or attachment_ids required"})
    return attachment_ids


def _run_attachment(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    records = client.read(
        model="ir.attachment",
        ids=_requested_attachment_ids(req),
        fields=["id", "name", "mimetype", "file_size", "res_model", "res_id", "create_date", "type", "url", "description"],
    )
    for record in records:
        record.pop("datas", None)
    return {"attachments": records, "count": len(records)}


def _run_content(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    metadata_fields = ["id", "name", "display_name", "create_date", "write_date"]
    content_fields = req.content_fields or ["body", "content", "message_body", "html_body", "note", "description"]
    records = client.search_read(
        model=req.model,
        domain=req.domain or [],
        fields=list(set(metadata_fields + content_fields)),
        limit=req.limit,
        offset=req.offset,
        order=req.order,
        include_ids=True,
    )
    for record in records:
        for field in content_fields:
            value = record.get(field)
            if not isinstance(value, str):
                continue
            if not req.raw_html:
                value = re.sub(r"<[^>]+>", "", value)
            record[field] = value[:req.max_content_chars] + "..." if len(value) > req.max_content_chars else value
    return {"model": req.model, "records": records, "count": len(records)}


def _run_message(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    if req.operation != "post":
        raise HTTPException(status_code=400, detail={"error": "unsupported_operation", "message": f"message mode: {req.operation}"})

    kwargs: dict[str, Any] = {
        "body": html.escape(req.body or "").replace("\n", "<br/>"),
        "message_type": req.message_type or "comment",
    }
    if req.subtype_xmlid:
        kwargs["subtype_xmlid"] = req.subtype_xmlid
    if req.partner_ids:
        kwargs["partner_ids"] = req.partner_ids
    message_attachment_ids = req.attachment_ids_for_message or req.attachment_ids
    if message_attachment_ids:
        kwargs["attachment_ids"] = message_attachment_ids

    result = client.call_with_transport(req.model, "message_post", args=[req.record_id], kwargs=kwargs)
    return {"operation": "post", "result": result}


def _create_record(client: OdooClient, req: OdooOpsRunnerRequest) -> tuple[Any, list[int]]:
    result = client.call_with_transport(req.model, "create", args=[req.values or {}], kwargs={})
    return result, [int(result)] if isinstance(result, int) else []


def _write_records(client: OdooClient, req: OdooOpsRunnerRequest) -> tuple[Any, list[int]]:
    if not req.ids:
        raise HTTPException(status_code=400, detail={"error": "write requires ids"})
    return client.call_with_transport(req.model, "write", args=[req.ids, req.values or {}], kwargs={}), req.ids


def _delete_records(client: OdooClient, req: OdooOpsRunnerRequest) -> tuple[Any, list[int]]:
    if not req.ids:
        raise HTTPException(status_code=400, detail={"error": "delete requires ids"})
    return client.call_with_transport(req.model, "unlink", args=[req.ids], kwargs={}), req.ids


def _verify_mutation(client: OdooClient, req: OdooOpsRunnerRequest, operation: str, affected_ids: list[int]) -> Any:
    if not affected_ids or operation == "delete":
        return None
    try:
        return client.read(req.model, affected_ids, ["id", "display_name"])
    except Exception:
        return None


def _run_mutation(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    operation = req.operation or (req.mode if req.mode in ("create", "write") else None)
    if operation == "create":
        result, affected_ids = _create_record(client, req)
    elif operation == "write":
        result, affected_ids = _write_records(client, req)
    elif operation == "delete":
        result, affected_ids = _delete_records(client, req)
    else:
        raise HTTPException(status_code=400, detail={"error": f"unknown mutation operation: {req.operation}"})
    return {
        "operation": operation,
        "affected_ids": affected_ids,
        "result": result,
        "verified": _verify_mutation(client, req, operation, affected_ids),
    }


def _run_execute(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    if not req.model or not req.method:
        raise HTTPException(status_code=400, detail={"error": "execute requires model and method"})
    result = client.call_with_transport(req.model, req.method, args=req.args or [], kwargs=req.kwargs or {})
    return {"model": req.model, "method": req.method, "result": result}


MODE_HANDLERS: dict[str, Callable[[OdooClient, OdooOpsRunnerRequest], dict[str, Any]]] = {
    "health": _run_health,
    "schema": _run_schema,
    "query": _run_query,
    "records": _run_query,
    "count": _run_count,
    "aggregate": _run_aggregate,
    "report": _run_report,
    "account_report": _run_report,
    "attachment": _run_attachment,
    "content": _run_content,
    "message": _run_message,
    "mutation": _run_mutation,
    "write": _run_mutation,
    "create": _run_mutation,
    "delete": _run_mutation,
    "execute": _run_execute,
}


@router.post("/run")
def odoo_ops_runner(req: OdooOpsRunnerRequest, _auth: dict = Depends(internal_api_key_auth)):
    """Consolidated Odoo command center. Routes by mode to the appropriate internal handler."""
    handler = MODE_HANDLERS.get(req.mode)
    if not handler:
        raise HTTPException(status_code=400, detail={"error": "unknown_mode", "message": f"Unknown mode: {req.mode}"})
    return handler(_get_client(req.credentials), req)
