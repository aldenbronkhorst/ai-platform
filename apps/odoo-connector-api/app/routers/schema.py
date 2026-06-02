from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import SchemaModelsRequest, SchemaFieldsRequest

router = APIRouter()


@router.post("/models")
def search_models(req: SchemaModelsRequest, auth: dict = Depends(internal_api_key_auth)):
    client = OdooClient(
        credentials=OdooCredentials(
            url=req.credentials.url,
            db=req.credentials.db,
            username=req.credentials.username,
            password_or_api_key=req.credentials.api_key,
        ),
        transport=req.credentials.transport,
    )
    domain = []
    if req.query:
        domain = ["|", ["model", "ilike", req.query], ["name", "ilike", req.query]]
    records = client.search_read(
        "ir.model",
        domain=domain,
        fields=["id", "model", "name", "state", "transient"],
        limit=req.limit,
        include_ids=True,
    )
    return {"mode": "search_models", "records": records}


@router.post("/fields")
def inspect_fields(req: SchemaFieldsRequest, auth: dict = Depends(internal_api_key_auth)):
    client = OdooClient(
        credentials=OdooCredentials(
            url=req.credentials.url,
            db=req.credentials.db,
            username=req.credentials.username,
            password_or_api_key=req.credentials.api_key,
        ),
        transport=req.credentials.transport,
    )
    result = client.fields_get(
        model=req.model,
        fields=req.fields or None,
        attributes=req.attributes or ["string", "type", "relation", "required", "readonly", "store"],
    )
    return result
