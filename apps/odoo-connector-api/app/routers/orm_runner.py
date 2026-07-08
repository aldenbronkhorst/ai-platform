"""Raw Odoo runner."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.errors import classify_odoo_error
from app.core.guidance import available_documents, document_markdown, guidance_payload
from app.core.odoo_client import OdooClient, OdooCredentials, OdooError
from app.core.security import internal_api_key_auth
from app.models.schemas import OdooCredentialsRequest

router = APIRouter()


class OdooRunRequest(BaseModel):
    credentials: Optional[OdooCredentialsRequest] = None
    operation: Optional[str] = None
    name: Optional[str] = None
    model: Optional[str] = None
    method: Optional[str] = None
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None
    calls: Optional[list[dict[str, Any]]] = None
    continue_on_error: bool = False


def _get_client(creds: OdooCredentialsRequest) -> OdooClient:
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url,
            db=creds.db,
            username=creds.username,
            password_or_api_key=creds.api_key,
        ),
    )


def _single_call(client: OdooClient, call: dict[str, Any], index: int | None = None) -> Any:
    model = call.get("model")
    method = call.get("method")
    if not model or not method:
        detail: dict[str, Any] = {
            "error": "odoo_call_requires_model_and_method",
            "message": "Odoo calls require model and method.",
        }
        if index is not None:
            detail["index"] = index
        raise HTTPException(status_code=400, detail=detail)

    try:
        result = client.execute_kw(
            model,
            method,
            args=call.get("args") or [],
            kwargs=call.get("kwargs") or {},
        )
    except OdooError as exc:
        # Classify centrally so an auth failure surfaces as 401 (not a generic 400)
        # and recognised field/delete errors get a specific error_type.
        status_code, detail = classify_odoo_error(exc)
        detail["model"] = model
        detail["method"] = method
        if index is not None:
            detail["index"] = index
        raise HTTPException(status_code=status_code, detail=detail) from exc
    if index is None:
        return result

    response: dict[str, Any] = {"index": index, "result": result}
    if call.get("name"):
        response["name"] = call["name"]
    return response


def _batch_error_entry(exc: Exception, index: int, call: dict[str, Any]) -> dict[str, Any]:
    """A sanitized per-call error record for the continue_on_error path.

    The raw Odoo/exception text is never echoed here (DLP): a bulk run can touch
    many records, so we keep only the classified error_type and a generic message.
    """
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        error_type = exc.detail.get("error_type", "HTTPException")
    else:
        error_type = type(exc).__name__
    return {
        "index": index,
        "name": call.get("name"),
        "model": call.get("model"),
        "method": call.get("method"),
        "error": True,
        "error_type": error_type,
        "message": "Odoo call failed.",
    }


def _abort_with_progress(exc: HTTPException, completed: list[Any], index: int) -> HTTPException:
    """Attach already-completed calls to a batch-abort error.

    Batches are NOT transactional: calls before the failure already executed
    against Odoo and are NOT rolled back. Without this the caller gets a bare
    error and no signal that earlier writes were applied.
    """
    detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    detail["aborted_at_index"] = index
    detail["completed_count"] = len(completed)
    detail["completed"] = completed
    detail["non_transactional"] = True
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.post("/run")
def odoo_runner(req: OdooRunRequest, _auth: dict = Depends(internal_api_key_auth)):
    if req.operation == "guidance":
        return guidance_payload()

    if req.operation == "playbook":
        name = (req.name or "").strip()
        markdown = document_markdown(name)
        if markdown is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "playbook_not_found",
                    "message": f"No troubleshooting document named '{name}'.",
                    "available": available_documents(),
                },
            )
        return {
            "connector": "odoo",
            "operation": "playbook",
            "name": name,
            "format": "markdown",
            "content": markdown,
        }

    if req.credentials is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "odoo_credentials_required",
                "message": "Odoo credentials are required for Odoo model calls.",
            },
        )

    client = _get_client(req.credentials)
    if req.calls is not None:
        results: list[Any] = []
        for index, call in enumerate(req.calls):
            try:
                results.append(_single_call(client, call, index=index))
            except HTTPException as exc:
                if not req.continue_on_error:
                    raise _abort_with_progress(exc, results, index) from exc
                results.append(_batch_error_entry(exc, index, call))
            except Exception as exc:  # unexpected non-Odoo failure
                if not req.continue_on_error:
                    raise
                results.append(_batch_error_entry(exc, index, call))
        return {"results": results, "count": len(results)}

    return _single_call(
        client,
        {
            "model": req.model,
            "method": req.method,
            "args": req.args or [],
            "kwargs": req.kwargs or {},
        },
    )
