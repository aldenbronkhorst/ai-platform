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


@router.post("/message")
async def handle_message(req: MessageRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)

    if req.operation == "post":
        if req.format == "plain":
            safe_body = html_mod.escape(req.body).replace("\n", "<br/>")
        else:
            safe_body = req.body

        if req.target_type == "record_chatter":
            if not req.model or not req.record_id:
                raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "record_chatter requires model and record_id"})
            kwargs = {
                "body": safe_body,
                "subtype_xmlid": req.subtype_xmlid,
                "message_type": req.message_type,
            }
            if req.partner_ids:
                kwargs["partner_ids"] = req.partner_ids
            if req.attachment_ids:
                kwargs["attachment_ids"] = req.attachment_ids
            result = client.call_with_transport(
                req.model, "message_post",
                args=[req.record_id],
                kwargs=kwargs,
            )
        elif req.target_type == "discuss_channel":
            if not req.channel_id:
                raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "discuss_channel requires channel_id"})
            kwargs = {"body": safe_body, "message_type": req.message_type}
            if req.partner_ids:
                kwargs["partner_ids"] = req.partner_ids
            if req.attachment_ids:
                kwargs["attachment_ids"] = req.attachment_ids
            result = client.call_with_transport(
                "discuss.channel", "message_post",
                args=[req.channel_id],
                kwargs=kwargs,
            )
        else:
            raise HTTPException(status_code=400, detail={"error": "unsupported_target", "message": f"post not supported for target: {req.target_type}"})

        if req.verify and result:
            try:
                mid = result.get("id") if isinstance(result, dict) else result
                verified = client.read("mail.message", [mid] if isinstance(mid, int) else [])
            except Exception:
                verified = None
        else:
            verified = None

        return {"operation": "post", "result": result, "verified": verified}

    if req.operation == "update":
        if not req.message_id:
            raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "update requires message_id"})
        safe_body = html_mod.escape(req.body).replace("\n", "<br/>") if req.format == "plain" else req.body
        result = client.call_with_transport(
            "mail.message", "write",
            args=[[req.message_id], {"body": safe_body}],
        )
        if req.verify:
            try:
                verified = client.read("mail.message", [req.message_id], ["id", "body", "author_id"])
            except Exception:
                verified = None
        else:
            verified = None
        return {"operation": "update", "message_id": req.message_id, "result": result, "verified": verified}

    raise HTTPException(status_code=400, detail={"error": "unknown_operation", "message": f"Unknown operation: {req.operation}"})
