import logging
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import ContentRequest

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


def _sanitize_html(text: str) -> str:
    import re
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    return text


@router.post("/content")
async def read_content(req: ContentRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)

    if req.mode == "thread":
        if not req.model or not req.ids:
            raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "thread mode requires model and ids"})
        messages = client.call_with_transport(
            req.model, "message_get",
            args=[req.ids, {"limit": req.limit, "offset": req.offset}],
        )
        return {"model": req.model, "record_ids": req.ids, "messages": messages or []}

    metadata_fields = req.metadata_fields or ["id", "name", "display_name", "create_date", "write_date"]

    if req.mode == "metadata":
        records = client.search_read(
            model=req.model,
            domain=req.domain or [],
            fields=metadata_fields,
            limit=req.limit,
            offset=req.offset,
            order=req.order,
            include_ids=True,
        )
        return {"model": req.model, "mode": "metadata", "records": records, "count": len(records)}

    if req.mode == "content":
        if not req.ids and req.limit > 5:
            raise HTTPException(status_code=400, detail={
                "error": "broad_content_refused",
                "message": "Content mode requires narrowed IDs or small limit (max 5 without IDs). Use metadata mode first to find relevant records.",
            })
        content_fields = req.content_fields or ["body", "content", "message_body", "html_body", "note", "description"]
        all_fields = list(set(metadata_fields + content_fields))
        records = client.search_read(
            model=req.model,
            domain=req.domain or [],
            fields=all_fields,
            limit=req.limit,
            offset=req.offset,
            order=req.order,
            include_ids=True,
        )
        for rec in records:
            for f in content_fields:
                if f in rec and isinstance(rec[f], str):
                    if not req.raw_html:
                        rec[f] = _sanitize_html(rec[f])
                    if len(rec[f]) > req.max_content_chars:
                        rec[f] = rec[f][:req.max_content_chars] + "..."
        return {"model": req.model, "mode": "content", "records": records, "count": len(records)}

    raise HTTPException(status_code=400, detail={"error": "unknown_mode", "message": f"Unknown mode: {req.mode}"})
