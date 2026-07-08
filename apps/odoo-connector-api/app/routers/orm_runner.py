"""Raw Odoo runner."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
        detail: dict[str, Any] = {
            "error": "odoo_call_failed",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "model": model,
            "method": method,
        }
        if index is not None:
            detail["index"] = index
        raise HTTPException(status_code=400, detail=detail) from exc
    if index is None:
        return result

    response: dict[str, Any] = {"index": index, "result": result}
    if call.get("name"):
        response["name"] = call["name"]
    return response


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
        results = []
        for index, call in enumerate(req.calls):
            try:
                results.append(_single_call(client, call, index=index))
            except Exception as exc:
                if not req.continue_on_error:
                    raise
                results.append(
                    {
                        "index": index,
                        "name": call.get("name"),
                        "model": call.get("model"),
                        "method": call.get("method"),
                        "error": True,
                        "error_type": type(exc).__name__,
                        "message": "Odoo call failed.",
                    }
                )
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
