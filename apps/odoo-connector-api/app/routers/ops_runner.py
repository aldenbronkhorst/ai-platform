"""Odoo operations runner — consolidated command center for all Odoo operations."""
import html
import logging
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from pydantic import BaseModel, Field
from typing import Any, Optional, Callable
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooAuthError, OdooClient, OdooCredentials
from app.models.schemas import OdooCredentialsRequest, OdooExecuteReportRequest
from app.services.odoo_report_service import OdooReportService

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_UNFILTERED_CONTENT_LIMIT = 10
MAX_SCHEMA_ERROR_CHARS = 400
INVALID_FIELD_PATTERNS = [
    re.compile(r"Invalid field (?P<model>[\w.]+)\.(?P<field>[\w_]+) in leaf", re.IGNORECASE),
    re.compile(r"Invalid field ['\"](?P<field>[\w_]+)['\"] on model ['\"](?P<model>[\w.]+)['\"]", re.IGNORECASE),
]
DOMAIN_LOGICAL_OPERATORS = {"&", "|", "!"}
X2MANY_FIELD_TYPES = {"many2many", "one2many"}
MANY2ONE_FIELD_TYPES = {"many2one"}
X2MANY_COMMANDS = {0, 1, 2, 3, 4, 5, 6}
RECORDSET_METHODS_REQUIRE_IDS = {
    "message_post",
    "message_subscribe",
    "message_unsubscribe",
    "action_feedback",
    "action_done",
    "action_cancel",
    "unlink",
    "write",
}
RECORDSET_METHOD_PREFIXES = ("action_", "button_", "message_")
MODEL_FIELD_ALIASES: dict[str, dict[str, str]] = {
    # mail.activity uses res_model/res_id; mail.message uses model/res_id.
    # Models commonly confuse the two when tracing chatter records.
    "mail.message": {"res_model": "model"},
}


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
    groupby: Optional[list[str]] = None


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url, db=creds.db, username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


def _odoo_base_url(req: OdooOpsRunnerRequest) -> str:
    raw_url = (req.credentials.url or "").strip().rstrip("/")
    if not raw_url:
        return ""

    parsed = urlsplit(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return raw_url[:-4] if raw_url.endswith("/web") else raw_url

    path = parsed.path.rstrip("/")
    if path == "/web":
        path = ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _odoo_record_url(req: OdooOpsRunnerRequest, model: str | None, record_id: int | None) -> str | None:
    if not model or not isinstance(record_id, int) or isinstance(record_id, bool):
        return None
    base_url = _odoo_base_url(req)
    if not base_url:
        return None
    fragment = urlencode({"id": record_id, "model": model, "view_type": "form"})
    return f"{base_url}/web#{fragment}"


def _record_urls_for_ids(req: OdooOpsRunnerRequest, record_ids: list[int]) -> list[dict[str, Any]]:
    urls = []
    for record_id in record_ids:
        record_url = _odoo_record_url(req, req.model, record_id)
        if record_url:
            urls.append({"id": record_id, "url": record_url})
    return urls


def _with_record_urls(req: OdooOpsRunnerRequest, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched_records = []
    for record in records:
        if not isinstance(record, dict):
            enriched_records.append(record)
            continue
        enriched_record = dict(record)
        record_url = _odoo_record_url(req, req.model, enriched_record.get("id"))
        if record_url:
            enriched_record["record_url"] = record_url
        enriched_records.append(enriched_record)
    return enriched_records


def _run_health(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    try:
        uid = client.authenticate()
        return {"status": "healthy", "authenticated": True, "user_id": uid, "database": req.credentials.db}
    except Exception as exc:
        return {"status": "error", "authenticated": False, "error": str(exc)}


def _compact_schema_error(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("Both Odoo API transports failed") or "Traceback" in message:
        return "Odoo could not inspect this model's schema."
    if len(message) > MAX_SCHEMA_ERROR_CHARS:
        return message[:MAX_SCHEMA_ERROR_CHARS].rstrip() + f"... [truncated {len(message) - MAX_SCHEMA_ERROR_CHARS} chars]"
    return message


def _schema_model_exists(client: OdooClient, model: str) -> bool | None:
    try:
        matches = client.search_read(
            model="ir.model",
            domain=[["model", "=", model]],
            fields=["model", "name"],
            limit=1,
            include_ids=True,
        )
    except OdooAuthError:
        raise
    except Exception:
        return None
    return bool(matches)


def _handled_schema_model_error(client: OdooClient, req: OdooOpsRunnerRequest, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, OdooAuthError):
        raise exc

    model = req.model or "unknown"
    model_exists = _schema_model_exists(client, model)
    if model_exists is False:
        error_type = "model_unavailable"
        message = f"Odoo model '{model}' is not installed or is not available to this connected account."
    else:
        error_type = "schema_unavailable"
        message = f"Odoo model '{model}' could not be inspected by this connected account."

    return {
        "model": model,
        "fields": {},
        "error": True,
        "handled": True,
        "status": "skipped",
        "error_type": error_type,
        "message": message,
        "model_exists": model_exists,
        "reason": _compact_schema_error(exc),
        "suggestion": "Use mode 'schema' with query to discover installed models, or inspect a different candidate model.",
    }


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
        try:
            return {"model": req.model, "fields": client.fields_get(req.model, fields=req.fields)}
        except Exception as exc:
            return _handled_schema_model_error(client, req, exc)
    return {"warning": "Provide model or query for schema inspection."}


def _invalid_field_error(exc: Exception) -> bool:
    return "Invalid field" in str(exc)


def _is_domain_operator(value: Any) -> bool:
    return isinstance(value, str) and value in DOMAIN_LOGICAL_OPERATORS


def _normalize_mixed_domain(domain: list[Any] | None) -> list[Any]:
    """Convert implicit-leading AND domains before explicit operators to Odoo prefix form.

    Odoo domains often allow consecutive leaf terms as an implicit AND, and
    explicit operators such as "|" are prefix operators. Model-generated domains
    can mix these styles, e.g. [A, "|", B, C]. Normalize that to ["&", A, "|", B, C]
    before sending it to Odoo, because some models return server tracebacks for
    the mixed form instead of a concise validation error.
    """
    if not domain:
        return []

    first_operator_index = next(
        (idx for idx, item in enumerate(domain) if _is_domain_operator(item)),
        None,
    )
    if first_operator_index is None or first_operator_index == 0:
        return domain

    leading_terms = domain[:first_operator_index]
    expression = list(domain[first_operator_index:])
    if not expression:
        return domain

    normalized = expression
    for term in reversed(leading_terms):
        normalized = ["&", term, *normalized]
    return normalized


def _model_field_aliases(model: str | None) -> dict[str, str]:
    return MODEL_FIELD_ALIASES.get(model or "", {})


def _normalize_fields(model: str | None, fields: list[str] | None) -> list[str] | None:
    if fields is None:
        return None
    aliases = _model_field_aliases(model)
    normalized = [aliases.get(field, field) for field in fields]
    return list(dict.fromkeys(normalized))


def _normalize_domain_field_aliases(model: str | None, domain: list[Any]) -> list[Any]:
    aliases = _model_field_aliases(model)
    if not aliases:
        return domain

    def normalize_item(item: Any) -> Any:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 3
            and isinstance(item[0], str)
            and item[0] not in DOMAIN_LOGICAL_OPERATORS
        ):
            normalized_leaf = list(item)
            normalized_leaf[0] = aliases.get(item[0], item[0])
            return normalized_leaf
        if isinstance(item, (list, tuple)):
            return [normalize_item(value) for value in item]
        return item

    return [normalize_item(item) for item in domain]


def _normalize_domain(model: str | None, domain: list[Any] | None) -> list[Any]:
    return _normalize_domain_field_aliases(model, _normalize_mixed_domain(domain or []))


def _invalid_field_info(exc: Exception) -> dict[str, str] | None:
    message = str(exc)
    for pattern in INVALID_FIELD_PATTERNS:
        match = pattern.search(message)
        if match:
            return {
                "model": match.group("model"),
                "field": match.group("field"),
            }
    return None


def _invalid_domain_field_response(req: OdooOpsRunnerRequest, info: dict[str, str]) -> HTTPException:
    model = info.get("model") or req.model or "unknown"
    field = info.get("field") or "unknown"
    return HTTPException(
        status_code=400,
        detail={
            "error": "invalid_domain_field",
            "error_type": "invalid_domain_field",
            "message": f"Field '{field}' does not exist on Odoo model '{model}'.",
            "model": model,
            "field": field,
            "suggestion": (
                "Run mode 'schema' for this model and retry with a valid field. "
                "For user attribution, prefer create_uid/write_uid when the target model supports them."
            ),
        },
    )


def _valid_query_fields(client: OdooClient, model: str, requested_fields: list[str]) -> tuple[list[str], list[str], dict[str, Any]]:
    requested_fields = _normalize_fields(model, requested_fields) or []
    schema = client.fields_get(model, fields=requested_fields)
    available_fields = set((schema.get("fields") or {}).keys())
    valid_fields = [field for field in requested_fields if field == "id" or field in available_fields]
    invalid_fields = [field for field in requested_fields if field not in valid_fields]
    return valid_fields, invalid_fields, schema


def _query_records(client: OdooClient, req: OdooOpsRunnerRequest, fields: list[str] | None = None) -> list[dict[str, Any]]:
    if req.ids:
        return client.read(model=req.model, ids=req.ids, fields=_normalize_fields(req.model, fields))
    domain = _normalize_domain(req.model, req.domain)
    return client.search_read(
        model=req.model,
        domain=domain,
        fields=_normalize_fields(req.model, fields),
        limit=req.limit,
        offset=req.offset,
        order=req.order,
        include_ids=req.include_ids,
    )


def _pagination_metadata(
    client: OdooClient,
    model: str,
    domain: list[Any],
    returned_count: int,
    limit: int,
    offset: int,
    ids: list[int] | None = None,
) -> dict[str, Any]:
    if ids:
        total_count = returned_count
    elif limit and offset == 0 and returned_count < limit:
        total_count = returned_count
    else:
        total_count = client.search_count(model=model, domain=domain or [])

    has_more = (offset + returned_count) < total_count
    return {
        "returned_count": returned_count,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "complete": not has_more,
    }


def _paged_result(
    client: OdooClient,
    req: OdooOpsRunnerRequest,
    records: list[dict[str, Any]],
    *,
    payload_key: str = "records",
) -> dict[str, Any]:
    returned_count = len(records)
    domain = _normalize_domain(req.model, req.domain)
    return {
        "model": req.model,
        payload_key: _with_record_urls(req, records),
        "count": returned_count,
        **_pagination_metadata(
            client,
            req.model,
            domain,
            returned_count,
            req.limit,
            req.offset,
            req.ids,
        ),
    }


def _run_query(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    try:
        records = _query_records(client, req, req.fields)
        return _paged_result(client, req, records)
    except Exception as exc:
        invalid_field = _invalid_field_info(exc)
        requested_fields = set(req.fields or [])
        if invalid_field and invalid_field["field"] not in requested_fields:
            raise _invalid_domain_field_response(req, invalid_field)
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

    try:
        records = _query_records(client, req, valid_fields)
    except Exception as exc:
        invalid_field = _invalid_field_info(exc)
        if invalid_field:
            raise _invalid_domain_field_response(req, invalid_field)
        raise
    return {
        **_paged_result(client, req, records),
        "warning": "Some requested fields do not exist on this Odoo model and were omitted.",
        "omitted_invalid_fields": invalid_fields,
        "field_errors": schema.get("field_errors"),
    }


def _run_count(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    return {
        "model": req.model,
        "count": client.search_count(
            model=req.model,
            domain=_normalize_domain(req.model, req.domain),
        ),
    }


def _run_aggregate(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    missing = [
        name
        for name, value in (("model", req.model), ("fields", req.fields))
        if not value
    ]
    if missing:
        return {
            "error": True,
            "handled": True,
            "status": "skipped",
            "error_type": "aggregate_arguments_required",
            "message": "Aggregate mode requires model and fields. groupby is optional and may be empty for a global aggregate.",
            "missing": missing,
            "suggestion": "Retry with model and aggregate fields such as ['amount_total_signed:sum']; include groupby only when grouped rows are needed.",
        }
    groupby = req.groupby if req.groupby is not None else (req.args or [])
    result = client.call_with_transport(
        req.model,
        "read_group",
        args=[_normalize_domain(req.model, req.domain), _normalize_fields(req.model, req.fields), groupby],
        kwargs={"lazy": True},
    )
    return {"model": req.model, "groupby": groupby, "groups": result}


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
    if not req.model:
        raise HTTPException(status_code=400, detail={"error": "content requires model"})

    metadata_fields = ["id", "name", "display_name", "create_date", "write_date"]
    content_fields = req.content_fields or ["body", "content", "message_body", "html_body", "note", "description"]
    requested_fields = list(dict.fromkeys(metadata_fields + content_fields))
    valid_fields, invalid_fields, schema = _valid_query_fields(client, req.model, requested_fields)
    valid_content_fields = [field for field in content_fields if field in valid_fields]
    if not valid_fields:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_content_fields",
            "message": "None of the requested content fields exist on this Odoo model.",
            "model": req.model,
            "invalid_fields": invalid_fields,
            "field_errors": schema.get("field_errors"),
        })

    limit = req.limit
    content_domain = list(req.domain or [])
    if req.ids:
        content_domain = [["id", "in", req.ids], *content_domain]
    content_domain = _normalize_domain(req.model, content_domain)
    warnings: list[str] = []
    if not content_domain and limit > MAX_UNFILTERED_CONTENT_LIMIT:
        limit = MAX_UNFILTERED_CONTENT_LIMIT
        warnings.append(
            "Unfiltered content reads are capped. Add a domain, ids, or a narrower limit for more records."
        )

    try:
        records = client.search_read(
            model=req.model,
            domain=content_domain,
            fields=_normalize_fields(req.model, valid_fields),
            limit=limit,
            offset=req.offset,
            order=req.order,
            include_ids=True,
        )
    except Exception as exc:
        invalid_field = _invalid_field_info(exc)
        if invalid_field:
            raise _invalid_domain_field_response(req, invalid_field)
        raise
    for record in records:
        for field in valid_content_fields:
            value = record.get(field)
            if not isinstance(value, str):
                continue
            if not req.raw_html:
                value = re.sub(r"<[^>]+>", "", value)
            record[field] = value[:req.max_content_chars] + "..." if len(value) > req.max_content_chars else value
    result = _paged_result(client, req.model_copy(update={"domain": content_domain, "limit": limit}), records)
    if invalid_fields:
        result["warning"] = "Some requested content fields do not exist on this Odoo model and were omitted."
        result["omitted_invalid_fields"] = invalid_fields
        result["field_errors"] = schema.get("field_errors")
    if warnings:
        result["content_warnings"] = warnings
    return result


def _run_message(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    operation = req.operation or "post"
    if operation != "post":
        raise HTTPException(status_code=400, detail={"error": "unsupported_operation", "message": f"message mode: {operation}"})
    if not req.model or not req.record_id:
        raise HTTPException(status_code=400, detail={
            "error": "message_target_required",
            "error_type": "message_target_required",
            "message": "Message mode requires model and record_id.",
            "missing": [name for name, value in (("model", req.model), ("record_id", req.record_id)) if not value],
        })

    body = req.body or ""
    kwargs: dict[str, Any] = {
        "body": body if req.raw_html else html.escape(body).replace("\n", "<br/>"),
        "message_type": req.message_type or "comment",
    }
    if req.subtype_xmlid:
        kwargs["subtype_xmlid"] = req.subtype_xmlid
    if req.partner_ids:
        kwargs["partner_ids"] = req.partner_ids
    message_attachment_ids = req.attachment_ids_for_message or req.attachment_ids
    if message_attachment_ids:
        kwargs["attachment_ids"] = message_attachment_ids

    result = client.call_with_transport(req.model, "message_post", args=[[req.record_id]], kwargs=kwargs)
    response: dict[str, Any] = {
        "operation": "post",
        "model": req.model,
        "record_id": req.record_id,
        "result": result,
    }
    record_url = _odoo_record_url(req, req.model, req.record_id)
    if record_url:
        response["record_url"] = record_url
    if isinstance(result, int) and not isinstance(result, bool):
        response["message_id"] = result
    return response


def _execute_method_requires_record_ids(method: str | None) -> bool:
    normalized = (method or "").strip()
    return normalized in RECORDSET_METHODS_REQUIRE_IDS or normalized.startswith(RECORDSET_METHOD_PREFIXES)


def _record_ids_from_execute_request(req: OdooOpsRunnerRequest) -> list[int]:
    if req.ids:
        return req.ids
    if req.record_id:
        return [req.record_id]
    return []


def _normalize_execute_args(req: OdooOpsRunnerRequest) -> list[Any]:
    args = list(req.args or [])
    if not _execute_method_requires_record_ids(req.method):
        return args

    if args:
        first_arg = args[0]
        if isinstance(first_arg, int) and not isinstance(first_arg, bool):
            return [[first_arg], *args[1:]]
        if _is_int_list(first_arg) and first_arg:
            return args
        record_ids = _record_ids_from_execute_request(req)
        if record_ids:
            return [record_ids, *args]
        raise HTTPException(status_code=400, detail={
            "error": "invalid_recordset_args",
            "error_type": "invalid_recordset_args",
            "message": (
                f"Odoo method '{req.method}' is a record method and requires record IDs as "
                "the first positional argument, e.g. args=[[123]], or via ids/record_id."
            ),
            "method": req.method,
            "model": req.model,
            "suggestion": "Retry with ids, record_id, or args whose first item is a non-empty list of record IDs.",
        })

    record_ids = _record_ids_from_execute_request(req)
    if record_ids:
        return [record_ids]

    raise HTTPException(status_code=400, detail={
        "error": "record_ids_required",
        "error_type": "record_ids_required",
        "message": (
            f"Odoo method '{req.method}' is a record method and cannot be called without target record IDs."
        ),
        "method": req.method,
        "model": req.model,
        "missing": ["ids", "record_id", "args[0]"],
        "suggestion": (
            "For chatter posts, prefer mode 'message' with model, record_id, operation='post', and body. "
            "For mail.activity completion, use mode 'execute' with model='mail.activity', method='action_feedback', ids=[activity_id], and kwargs.feedback."
        ),
    })


def _is_int_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value)


def _is_x2many_command(value: Any) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 3
        and isinstance(value[0], int)
        and value[0] in X2MANY_COMMANDS
    )


def _is_x2many_command_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_x2many_command(item) for item in value)


def _normalize_x2many_value(field_name: str, value: Any) -> Any:
    if value is None or value is False:
        return value
    if not isinstance(value, list):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_x2many_value",
                "error_type": "invalid_x2many_value",
                "field": field_name,
                "message": f"Field '{field_name}' expects Odoo x2many command syntax.",
            },
        )
    if not value:
        return value
    if _is_x2many_command(value):
        return [value]
    if _is_int_list(value):
        return [[6, 0, value]]
    if _is_x2many_command_list(value):
        return value
    raise HTTPException(
        status_code=400,
        detail={
            "error": "invalid_x2many_value",
            "error_type": "invalid_x2many_value",
            "field": field_name,
            "message": (
                f"Field '{field_name}' expects a list of Odoo command triples, "
                "for example [[6, 0, [1, 2]]]."
            ),
        },
    )


def _normalize_many2one_value(value: Any) -> Any:
    if isinstance(value, list) and value and isinstance(value[0], int):
        return value[0]
    if isinstance(value, dict) and isinstance(value.get("id"), int):
        return value["id"]
    return value


def _needs_mutation_schema(values: dict[str, Any]) -> bool:
    return any(isinstance(value, (list, dict)) for value in values.values())


def _mutation_field_schema(client: OdooClient, model: str, values: dict[str, Any]) -> dict[str, Any]:
    if not values or not _needs_mutation_schema(values):
        return {}
    schema = client.fields_get(model, fields=list(values.keys()), attributes=["type", "relation", "readonly"])
    fields = schema.get("fields") if isinstance(schema, dict) else None
    return fields if isinstance(fields, dict) else {}


def _normalize_mutation_values(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    values = dict(req.values or {})
    if not req.model or not values:
        return values
    field_schema = _mutation_field_schema(client, req.model, values)
    if not field_schema:
        return values

    normalized = dict(values)
    for field_name, value in values.items():
        info = field_schema.get(field_name)
        if not isinstance(info, dict):
            continue
        field_type = str(info.get("type") or "")
        if field_type in X2MANY_FIELD_TYPES:
            normalized[field_name] = _normalize_x2many_value(field_name, value)
        elif field_type in MANY2ONE_FIELD_TYPES:
            normalized[field_name] = _normalize_many2one_value(value)
    return normalized


def _create_record(client: OdooClient, req: OdooOpsRunnerRequest) -> tuple[Any, list[int]]:
    result = client.call_with_transport(req.model, "create", args=[_normalize_mutation_values(client, req)], kwargs={})
    return result, [int(result)] if isinstance(result, int) else []


def _write_records(client: OdooClient, req: OdooOpsRunnerRequest) -> tuple[Any, list[int]]:
    if not req.ids:
        raise HTTPException(status_code=400, detail={"error": "write requires ids"})
    return client.call_with_transport(req.model, "write", args=[req.ids, _normalize_mutation_values(client, req)], kwargs={}), req.ids


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
    response = {
        "operation": operation,
        "affected_ids": affected_ids,
        "result": result,
        "verified": _verify_mutation(client, req, operation, affected_ids),
    }
    record_urls = _record_urls_for_ids(req, affected_ids)
    if record_urls:
        response["record_urls"] = record_urls
        if len(record_urls) == 1:
            response["record_url"] = record_urls[0]["url"]
    return response


def _run_execute(client: OdooClient, req: OdooOpsRunnerRequest) -> dict[str, Any]:
    if not req.model or not req.method:
        raise HTTPException(status_code=400, detail={"error": "execute requires model and method"})
    if req.method == "search_read":
        kwargs = req.kwargs or {}
        args = req.args or []
        domain = req.domain if req.domain is not None else (args[0] if args and isinstance(args[0], list) else [])
        domain = _normalize_domain(req.model, domain)
        fields = req.fields or kwargs.get("fields")
        if not fields and len(args) > 1 and isinstance(args[1], list):
            fields = args[1]
        limit = int(kwargs.get("limit") or req.limit)
        offset = int(kwargs.get("offset") or req.offset)
        order = kwargs.get("order") or req.order
        records = client.search_read(
            model=req.model,
            domain=domain,
            fields=_normalize_fields(req.model, fields),
            limit=limit,
            offset=offset,
            order=order,
            include_ids=req.include_ids,
        )
        returned_count = len(records)
        return {
            "model": req.model,
            "method": req.method,
            "result": _with_record_urls(req, records),
            "count": returned_count,
            **_pagination_metadata(client, req.model, domain, returned_count, limit, offset),
        }
    args = _normalize_execute_args(req)
    result = client.call_with_transport(req.model, req.method, args=args, kwargs=req.kwargs or {})
    response: dict[str, Any] = {"model": req.model, "method": req.method, "result": result}
    if _execute_method_requires_record_ids(req.method):
        record_ids = _record_ids_from_execute_request(req)
        if not record_ids and args and _is_int_list(args[0]):
            record_ids = args[0]
        if record_ids:
            response["record_ids"] = record_ids
            record_urls = _record_urls_for_ids(req, record_ids)
            if record_urls:
                response["record_urls"] = record_urls
                if len(record_urls) == 1:
                    response["record_url"] = record_urls[0]["url"]
    if req.method == "message_post" and isinstance(result, int) and not isinstance(result, bool):
        response["message_id"] = result
    return response


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
