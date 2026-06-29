"""Raw Odoo runner."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.odoo_client import OdooClient, OdooCredentials, OdooError
from app.core.security import internal_api_key_auth
from app.models.schemas import OdooCredentialsRequest

router = APIRouter()


class OdooRunRequest(BaseModel):
    credentials: OdooCredentialsRequest
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
