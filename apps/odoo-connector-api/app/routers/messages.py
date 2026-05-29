import html
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.core.formatting import format_message_response
from app.models.schemas import MessageListRequest, MessageCreateRequest

router = APIRouter()


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url,
            db=creds.db,
            username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


@router.post("/list")
async def list_messages(req: MessageListRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    domain = req.domain or []
    if req.model and req.record_id:
        domain += [["model", "=", req.model], ["res_id", "=", req.record_id]]
    records = client.search_read(
        "mail.message",
        domain=domain,
        fields=["id", "body", "date", "author_id", "subject", "message_type", "subtype_id"],
        limit=req.limit,
        include_ids=True,
    )
    return {
        "messages": [format_message_response(r) for r in records],
    }


@router.post("/create")
async def create_message(req: MessageCreateRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)

    rendered_body = "<br/>".join(html.escape(req.body).splitlines())

    kwargs = {
        "body": rendered_body,
        "message_type": req.message_type,
        "subtype_xmlid": req.subtype_xmlid,
    }
    if req.partner_ids:
        kwargs["partner_ids"] = req.partner_ids
    if req.attachment_ids:
        kwargs["attachment_ids"] = req.attachment_ids

    result = client.call_with_transport(
        req.model,
        "message_post",
        args=[[req.record_id]],
        kwargs=kwargs,
    )

    return {
        "message_id": result,
        "model": req.model,
        "record_id": req.record_id,
        "status": "posted",
    }
