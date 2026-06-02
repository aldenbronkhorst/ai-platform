"""Odoo operations runner — consolidated command center for all Odoo operations."""
import logging
from pydantic import BaseModel, Field
from typing import Any, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import OdooCredentialsRequest
from app.services.odoo_report_service import OdooReportService

router = APIRouter()
logger = logging.getLogger(__name__)

REFUSED_CONTENT_FIELDS = {"body", "content", "message_body", "html_body", "note", "description"}


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
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
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


@router.post("/run")
async def odoo_ops_runner(req: OdooOpsRunnerRequest, auth: dict = Depends(internal_api_key_auth)):
    """Consolidated Odoo command center. Routes by mode to the appropriate internal handler."""
    client = _get_client(req.credentials)

    if req.mode == "health":
        try:
            uid = client.authenticate()
            return {"status": "healthy", "authenticated": True, "user_id": uid, "database": req.credentials.db}
        except Exception as e:
            return {"status": "error", "authenticated": False, "error": str(e)}

    elif req.mode == "schema":
        if req.query:
            models = client.call_with_transport("ir.model", "search_read",
                args=[[["model", "ilike", req.query]], ["model", "name"]],
                kwargs={"limit": req.limit}) or []
            return {"models": models}
        if req.model and req.fields:
            fields_info = client.fields_get(req.model, fields=req.fields)
            return {"model": req.model, "fields": fields_info}
        if req.model:
            fields_info = client.fields_get(req.model)
            return {"model": req.model, "fields": fields_info}
        return {"warning": "Provide model or query for schema inspection."}

    elif req.mode in ("query", "records"):
        if req.ids:
            records = client.read(model=req.model, ids=req.ids, fields=req.fields)
            return {"model": req.model, "records": records, "count": len(records)}
        records = client.search_read(
            model=req.model, domain=req.domain or [],
            fields=req.fields, limit=req.limit, offset=req.offset,
            order=req.order, include_ids=req.include_ids,
        )
        return {"model": req.model, "records": records, "count": len(records)}

    elif req.mode == "count":
        count = client.search_count(model=req.model, domain=req.domain or [])
        return {"model": req.model, "count": count}

    elif req.mode == "aggregate":
        if not req.model or not req.fields or not req.args:
            raise HTTPException(status_code=400, detail={"error": "aggregate requires model, fields, and groupby"})
        result = client.call_with_transport(req.model, "read_group",
            args=[req.domain or [], req.fields, req.args],
            kwargs={"lazy": True})
        return {"model": req.model, "groups": result}

    elif req.mode in ("report", "account_report"):
        from app.models.schemas import OdooExecuteReportRequest
        report_req = OdooExecuteReportRequest(
            credentials=req.credentials,
            report_name=req.report_name or "",
            report_id=req.report_id,
            date_from=req.date_from, date_to=req.date_to,
            company_id=req.company_id,
            timezone=req.timezone, lang=req.lang,
            line_names=req.line_names,
            include_raw_lines=req.include_raw_lines,
        )
        service = OdooReportService(client)
        return service.execute(report_req)

    elif req.mode == "attachment":
        all_ids = []
        if req.attachment_id:
            all_ids.append(req.attachment_id)
        if req.attachment_ids:
            all_ids.extend(req.attachment_ids)
        if not all_ids:
            raise HTTPException(status_code=400, detail={"error": "attachment_id or attachment_ids required"})
        records = client.read(
            model="ir.attachment",
            ids=all_ids,
            fields=["id", "name", "mimetype", "file_size", "res_model", "res_id", "create_date", "type", "url", "description"],
        )
        for rec in records:
            rec.pop("datas", None)
        return {"attachments": records, "count": len(records)}

    elif req.mode == "content":
        if req.mode == "content" and not req.ids and req.limit > 5:
            raise HTTPException(status_code=400, detail={
                "error": "broad_content_refused",
                "message": "Content mode requires narrowed IDs or small limit.",
            })
        metadata_f = ["id", "name", "display_name", "create_date", "write_date"]
        content_f = req.content_fields or ["body", "content", "message_body", "html_body", "note", "description"]
        all_fields = list(set(metadata_f + content_f))
        records = client.search_read(
            model=req.model, domain=req.domain or [],
            fields=all_fields, limit=req.limit, offset=req.offset,
            order=req.order, include_ids=True,
        )
        if req.mode == "content":
            for rec in records:
                for f in content_f:
                    if f in rec and isinstance(rec[f], str):
                        if not req.raw_html:
                            import re as _re
                            rec[f] = _re.sub(r'<[^>]+>', '', rec[f])
                        if len(rec[f]) > req.max_content_chars:
                            rec[f] = rec[f][:req.max_content_chars] + "..."
        return {"model": req.model, "records": records, "count": len(records)}

    elif req.mode == "message":
        import html as _html
        if req.operation == "post":
            safe_body = _html.escape(req.body or "").replace("\n", "<br/>")
            kwargs = {"body": safe_body, "message_type": req.message_type or "comment"}
            if req.subtype_xmlid:
                kwargs["subtype_xmlid"] = req.subtype_xmlid
            result = client.call_with_transport(req.model, "message_post", args=[req.record_id], kwargs=kwargs)
            return {"operation": "post", "result": result}
        raise HTTPException(status_code=400, detail={"error": "unsupported_operation", "message": f"message mode: {req.operation}"})

    elif req.mode in ("mutation", "write", "create"):
        if req.operation == "create":
            result = client.call_with_transport(req.model, "create", args=[req.values or {}], kwargs={})
            affected_ids = [int(result)] if isinstance(result, int) else []
        elif req.operation == "write":
            if not req.ids:
                raise HTTPException(status_code=400, detail={"error": "write requires ids"})
            result = client.call_with_transport(req.model, "write", args=[req.ids, req.values or {}], kwargs={})
            affected_ids = req.ids
        elif req.operation == "delete":
            if not req.ids:
                raise HTTPException(status_code=400, detail={"error": "delete requires ids"})
            result = client.call_with_transport(req.model, "unlink", args=[req.ids], kwargs={})
            affected_ids = req.ids
        else:
            raise HTTPException(status_code=400, detail={"error": f"unknown mutation operation: {req.operation}"})
        verified = None
        if affected_ids and req.operation != "delete":
            try:
                verified = client.read(req.model, affected_ids, ["id", "display_name"])
            except Exception:
                pass
        return {"operation": req.operation, "affected_ids": affected_ids, "result": result, "verified": verified}

    elif req.mode == "execute":
        if not req.model or not req.method:
            raise HTTPException(status_code=400, detail={"error": "execute requires model and method"})
        result = client.call_with_transport(req.model, req.method, args=req.args or [], kwargs=req.kwargs or {})
        return {"model": req.model, "method": req.method, "result": result}

    raise HTTPException(status_code=400, detail={"error": "unknown_mode", "message": f"Unknown mode: {req.mode}"})
