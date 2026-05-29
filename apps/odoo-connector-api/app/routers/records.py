from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import (
    RecordsSearchReadRequest,
    RecordsCountRequest,
    RecordsReadRequest,
    RecordsMutateRequest,
)

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


@router.post("/search-read")
async def search_read(req: RecordsSearchReadRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    records = client.search_read(
        model=req.model,
        domain=req.domain,
        fields=req.fields,
        limit=req.limit,
        offset=req.offset,
        order=req.order,
        include_ids=req.include_ids,
    )
    return {"model": req.model, "records": records}


@router.post("/count")
async def count_records(req: RecordsCountRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    count = client.search_count(model=req.model, domain=req.domain)
    return {"model": req.model, "count": count}


@router.post("/read")
async def read_records(req: RecordsReadRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    records = client.read(model=req.model, ids=req.ids, fields=req.fields)
    return {"model": req.model, "records": records}


@router.post("/mutate")
async def mutate_records(req: RecordsMutateRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)

    if req.dry_run:
        return {"dry_run": True, "would_execute": req.model_dump()}

    operation = req.operation.strip().lower()
    if operation not in {"create", "write", "delete", "workflow"}:
        raise HTTPException(status_code=400, detail="operation must be create/write/delete/workflow")

    if operation == "create":
        result = client.call_with_transport(req.model, "create", args=[req.values or {}], kwargs={})
        affected_ids = [int(result)] if isinstance(result, int) else []
    elif operation == "write":
        if not req.record_ids:
            raise HTTPException(status_code=400, detail="record_ids required for write")
        result = client.call_with_transport(req.model, "write", args=[req.record_ids, req.values or {}], kwargs={})
        affected_ids = req.record_ids
    elif operation == "delete":
        if not req.record_ids:
            raise HTTPException(status_code=400, detail="record_ids required for delete")
        result = client.call_with_transport(req.model, "unlink", args=[req.record_ids], kwargs={})
        affected_ids = req.record_ids
    else:  # workflow
        if not req.record_ids:
            raise HTTPException(status_code=400, detail="record_ids required for workflow")
        method = str(req.workflow_method or "").strip()
        if not method or method.startswith("_"):
            raise HTTPException(status_code=400, detail="workflow_method must be a public method name")
        result = client.call_with_transport(req.model, method, args=[req.record_ids], kwargs={})
        affected_ids = req.record_ids

    output = {
        "model": req.model,
        "operation": operation,
        "result": result,
        "record_ids": affected_ids,
    }

    if req.verify and affected_ids and operation != "delete":
        verify_fields = req.verify_fields or ["id", "display_name"]
        output["verified_records"] = client.read(req.model, affected_ids, verify_fields)

    return output
