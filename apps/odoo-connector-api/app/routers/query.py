import logging
from fastapi import APIRouter, Depends
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import QueryRequest

router = APIRouter()
logger = logging.getLogger(__name__)

REFUSED_FIELDS = {"body", "content", "message_body", "html_body", "note", "description"}


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url, db=creds.db, username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


def _check_refused_fields(fields: list[str] | None):
    if not fields:
        return
    refused = [f for f in fields if f.lower() in REFUSED_FIELDS or f.lower().endswith(("_body", "_content", "_html"))]
    if refused:
        raise ValueError(
            f"Fields {refused} contain binary/content data and cannot be fetched via odoo_query. "
            f"Use odoo_content or odoo_attachment instead."
        )


@router.post("/query")
async def query(req: QueryRequest, auth: dict = Depends(internal_api_key_auth)):
    _check_refused_fields(req.fields)
    client = _get_client(req.credentials)

    if req.mode == "count":
        count = client.search_count(model=req.model, domain=req.domain or [])
        return {"model": req.model, "count": count}

    if req.mode == "ids":
        ids = client.call_with_transport(
            req.model, "search", args=[req.domain or []],
            kwargs={"limit": req.limit, "offset": req.offset} if not req.order else {"limit": req.limit, "offset": req.offset, "order": req.order},
        )
        return {"model": req.model, "ids": ids, "count": len(ids)}

    records = client.search_read(
        model=req.model,
        domain=req.domain or [],
        fields=req.fields,
        limit=req.limit,
        offset=req.offset,
        order=req.order,
        include_ids=req.include_ids,
    )

    if req.mode == "summary":
        count = client.search_count(model=req.model, domain=req.domain or [])
        return {"model": req.model, "count": count, "sample": records[:req.sample_size or 3], "total_samples": len(records)}

    return {"model": req.model, "records": records, "count": len(records)}
