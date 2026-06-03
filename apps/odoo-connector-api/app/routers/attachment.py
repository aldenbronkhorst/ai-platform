import logging
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import AttachmentRequest

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


@router.post("/attachment")
def handle_attachment(req: AttachmentRequest, _auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    all_ids = []
    if req.attachment_id:
        all_ids.append(req.attachment_id)
    if req.attachment_ids:
        all_ids.extend(req.attachment_ids)
    if not all_ids:
        raise HTTPException(status_code=400, detail={"error": "missing_ids", "message": "attachment_id or attachment_ids required"})

    if req.mode == "metadata":
        records = client.search_read(
            model="ir.attachment",
            domain=[("id", "in", all_ids)],
            fields=["id", "name", "mimetype", "file_size", "res_model", "res_id", "description", "create_date"],
            include_ids=True,
        )
        return {"attachments": records, "count": len(records)}

    if req.mode == "link":
        records = client.read(
            model="ir.attachment",
            ids=all_ids,
            fields=["id", "name", "mimetype", "file_size", "type", "url", "res_model", "res_id"],
        )
        return {"attachments": records, "count": len(records)}

    if req.mode in ("text", "content"):
        records = client.read(
            model="ir.attachment",
            ids=all_ids,
            fields=["id", "name", "mimetype", "file_size", "index_content", "type", "url"],
        )
        for rec in records:
            if rec.get("index_content"):
                ic = rec["index_content"]
                if isinstance(ic, str) and len(ic) > req.max_text_chars:
                    rec["index_content"] = ic[:req.max_text_chars] + "..."
            rec.pop("datas", None)
        return {"attachments": records, "count": len(records)}

    if req.mode == "base64":
        records = client.read(
            model="ir.attachment",
            ids=all_ids,
            fields=["id", "name", "mimetype", "file_size", "type"],
        )
        for rec in records:
            rec.pop("datas", None)
        return {"attachments": records, "count": len(records)}

    if req.mode == "analyze":
        records = client.read(
            model="ir.attachment",
            ids=all_ids,
            fields=["id", "name", "mimetype", "file_size", "index_content", "description", "res_model", "res_id", "create_date", "type", "url"],
        )
        for rec in records:
            if rec.get("index_content"):
                ic = rec["index_content"]
                if isinstance(ic, str) and len(ic) > req.max_text_chars:
                    rec["index_content"] = ic[:req.max_text_chars] + "..."
            rec.pop("datas", None)
        return {"attachments": records, "count": len(records)}

    raise HTTPException(status_code=400, detail={"error": "unknown_mode", "message": f"Unknown mode: {req.mode}"})
