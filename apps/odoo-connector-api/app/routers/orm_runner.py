"""Raw Odoo ORM runner."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.odoo_client import OdooClient, OdooCredentials
from app.core.security import internal_api_key_auth
from app.models.schemas import OdooCredentialsRequest

router = APIRouter()


class OdooOrmRequest(BaseModel):
    credentials: OdooCredentialsRequest
    mode: Optional[str] = None
    model: Optional[str] = None
    method: Optional[str] = None
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None
    json2_payload: Optional[dict[str, Any]] = None
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
        transport=creds.transport,
    )


def _single_call(client: OdooClient, call: dict[str, Any], index: int | None = None) -> dict[str, Any]:
    model = call.get("model")
    method = call.get("method")
    if not model or not method:
        detail: dict[str, Any] = {
            "error": "orm_call_requires_model_and_method",
            "message": "Odoo ORM calls require model and method.",
        }
        if index is not None:
            detail["index"] = index
        raise HTTPException(status_code=400, detail=detail)

    result = client.call_with_transport(
        model,
        method,
        args=call.get("args") or [],
        kwargs=call.get("kwargs") or {},
        json2_payload=call.get("json2_payload"),
    )
    response = {
        "model": model,
        "method": method,
        "transport": client.last_transport,
        "result": result,
    }
    if index is not None:
        response["index"] = index
    if call.get("name"):
        response["name"] = call["name"]
    return response


@router.post("/run")
def odoo_orm_runner(req: OdooOrmRequest, _auth: dict = Depends(internal_api_key_auth)):
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
                        "message": "Odoo ORM call failed.",
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
            "json2_payload": req.json2_payload,
        },
    )
