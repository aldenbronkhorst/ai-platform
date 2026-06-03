import logging
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials, OdooAuthError
from app.models.schemas import MutationRequest
from app.core.formatting import format_mutation_response

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


def _execute_operation(client, operation, model, record_ids, values, workflow_method, dry_run, verify, verify_fields):
    if dry_run:
        return {"dry_run": True, "operation": operation, "model": model,
                "record_ids": record_ids, "values": values, "workflow_method": workflow_method}

    if operation == "create":
        result = client.call_with_transport(model, "create", args=[values or {}], kwargs={})
        affected_ids = [int(result)] if isinstance(result, int) else []
    elif operation == "write":
        if not record_ids:
            raise HTTPException(status_code=400, detail={"error": "missing_ids", "message": "record_ids required for write"})
        result = client.call_with_transport(model, "write", args=[record_ids, values or {}], kwargs={})
        affected_ids = record_ids
    elif operation == "delete":
        if not record_ids:
            raise HTTPException(status_code=400, detail={"error": "missing_ids", "message": "record_ids required for delete"})
        result = client.call_with_transport(model, "unlink", args=[record_ids], kwargs={})
        affected_ids = record_ids
    else:
        if not record_ids:
            raise HTTPException(status_code=400, detail={"error": "missing_ids", "message": "record_ids required for workflow"})
        if not workflow_method:
            raise HTTPException(status_code=400, detail={"error": "missing_method", "message": "workflow_method is required"})
        result = client.call_with_transport(model, workflow_method, args=[record_ids], kwargs={})
        affected_ids = record_ids

    verified = None
    if verify and affected_ids and operation != "delete":
        vfields = verify_fields or ["id", "display_name"]
        try:
            verified = client.read(model, affected_ids, vfields)
        except Exception as e:
            logger.warning("Verify failed after %s: %s", operation, e)

    return format_mutation_response(
        model=model, operation=operation, result=result,
        record_ids=affected_ids, verified_records=verified,
    )


@router.post("/mutation")
def mutate(req: MutationRequest, _auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)

    if req.items:
        results = []
        for item in req.items:
            try:
                res = _execute_operation(
                    client, item.operation, item.model, item.record_ids, item.values,
                    item.workflow_method, req.dry_run, req.verify, req.verify_fields,
                )
                results.append(res)
            except OdooAuthError:
                raise
            except Exception as e:
                if req.continue_on_error:
                    results.append({"error": True, "model": item.model, "operation": item.operation, "message": str(e)})
                else:
                    raise
        return {"results": results, "batch": True}

    return _execute_operation(
        client, req.operation, req.model, req.record_ids, req.values,
        req.workflow_method, req.dry_run, req.verify, req.verify_fields,
    )
