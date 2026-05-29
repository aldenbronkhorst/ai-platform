import base64
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import AttachmentListRequest, AttachmentGetRequest, AttachmentCreateRequest

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
async def list_attachments(req: AttachmentListRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    domain = req.domain or []
    if req.model and req.record_id:
        domain += [["res_model", "=", req.model], ["res_id", "=", req.record_id]]
    records = client.search_read(
        "ir.attachment",
        domain=domain,
        fields=["id", "name", "mimetype", "res_model", "res_id", "create_date", "file_size"],
        limit=req.limit,
        include_ids=True,
    )
    return {"attachments": records}


@router.post("/get")
async def get_attachment(req: AttachmentGetRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    records = client.read(
        "ir.attachment",
        [req.attachment_id],
        fields=["id", "name", "mimetype", "res_model", "res_id", "datas", "index_content"],
    )
    if not records:
        raise HTTPException(status_code=404, detail="Attachment not found")

    record = dict(records[0])
    raw_b64 = record.pop("datas", None)

    if req.mode == "metadata":
        return record

    data = base64.b64decode(raw_b64) if isinstance(raw_b64, str) else b""

    if req.mode == "base64":
        record["content_base64"] = base64.b64encode(data).decode()
        return record

    if req.mode == "text":
        text = ""
        if record.get("index_content"):
            text = str(record.get("index_content") or "")
            record["text_source"] = "index_content"
        record["text"] = text
        return record

    return record


@router.post("/create")
async def create_attachment(req: AttachmentCreateRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    values = {
        "name": req.filename,
        "datas": req.content_base64,
        "res_model": req.model,
        "res_id": req.record_id,
    }
    if req.mimetype:
        values["mimetype"] = req.mimetype

    result = client.call_with_transport("ir.attachment", "create", args=[values], kwargs={})
    return {"attachment_id": result, "model": req.model, "record_id": req.record_id}
