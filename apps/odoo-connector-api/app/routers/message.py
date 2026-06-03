import html as html_mod
import logging
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import MessageRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url, db=creds.db, username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


def _safe_body(req: MessageRequest) -> str:
    return html_mod.escape(req.body).replace("\n", "<br/>") if req.format == "plain" else req.body


def _message_kwargs(req: MessageRequest, safe_body: str) -> dict:
    kwargs = {"body": safe_body, "message_type": req.message_type}
    if req.partner_ids:
        kwargs["partner_ids"] = req.partner_ids
    if req.attachment_ids:
        kwargs["attachment_ids"] = req.attachment_ids
    return kwargs


def _post_to_record_chatter(client: OdooClient, req: MessageRequest, safe_body: str):
    if not req.model or not req.record_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_params", "message": "record_chatter requires model and record_id"},
        )
    kwargs = _message_kwargs(req, safe_body)
    kwargs["subtype_xmlid"] = req.subtype_xmlid
    return client.call_with_transport(req.model, "message_post", args=[req.record_id], kwargs=kwargs)


def _post_to_discuss_channel(client: OdooClient, req: MessageRequest, safe_body: str):
    if not req.channel_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_params", "message": "discuss_channel requires channel_id"},
        )
    return client.call_with_transport(
        "discuss.channel",
        "message_post",
        args=[req.channel_id],
        kwargs=_message_kwargs(req, safe_body),
    )


def _verify_post(client: OdooClient, result):
    if not result:
        return None
    try:
        message_id = result.get("id") if isinstance(result, dict) else result
        return client.read("mail.message", [message_id] if isinstance(message_id, int) else [])
    except Exception:
        return None


def _verify_update(client: OdooClient, message_id: int):
    try:
        return client.read("mail.message", [message_id], ["id", "body", "author_id"])
    except Exception:
        return None


def _handle_post(client: OdooClient, req: MessageRequest) -> dict:
    safe_body = _safe_body(req)
    if req.target_type == "record_chatter":
        result = _post_to_record_chatter(client, req, safe_body)
    elif req.target_type == "discuss_channel":
        result = _post_to_discuss_channel(client, req, safe_body)
    else:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_target", "message": f"post not supported for target: {req.target_type}"},
        )
    return {"operation": "post", "result": result, "verified": _verify_post(client, result) if req.verify else None}


def _handle_update(client: OdooClient, req: MessageRequest) -> dict:
    if not req.message_id:
        raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "update requires message_id"})
    result = client.call_with_transport("mail.message", "write", args=[[req.message_id], {"body": _safe_body(req)}])
    verified = _verify_update(client, req.message_id) if req.verify else None
    return {"operation": "update", "message_id": req.message_id, "result": result, "verified": verified}


@router.post("/message")
def handle_message(req: MessageRequest, _auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    if req.operation == "post":
        return _handle_post(client, req)
    if req.operation == "update":
        return _handle_update(client, req)
    raise HTTPException(status_code=400, detail={"error": "unknown_operation", "message": f"Unknown operation: {req.operation}"})
