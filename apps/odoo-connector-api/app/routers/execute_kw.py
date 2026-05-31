from fastapi import APIRouter, Depends, HTTPException
from app.core.config import get_settings
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import ExecuteKwRequest

BLOCKED_EXECUTE_KW_METHODS = {"unlink", "sudo", "with_context", "env", "__import__"}

router = APIRouter()


@router.post("/")
async def execute_kw(req: ExecuteKwRequest, auth: dict = Depends(internal_api_key_auth)):
    settings = get_settings()

    if not settings.execute_kw_allow_write_methods:
        raise HTTPException(status_code=403, detail="execute_kw is disabled. Enable EXECUTE_KW_ALLOW_WRITE to allow write operations.")

    blocked = set()
    if settings.execute_kw_blocked_methods:
        blocked = {m.strip() for m in settings.execute_kw_blocked_methods.split(",")}
    blocked |= BLOCKED_EXECUTE_KW_METHODS

    if req.method in blocked:
        raise HTTPException(status_code=403, detail=f"Method '{req.method}' is blocked.")

    settings = get_settings()
    client = OdooClient(
        credentials=OdooCredentials(
            url=req.credentials.url,
            db=req.credentials.db,
            username=req.credentials.username,
            password_or_api_key=req.credentials.api_key,
        ),
        transport=req.credentials.transport,
        timeout=settings.odoo_api_timeout_seconds,
        ssl_verify=settings.odoo_ssl_verify,
    )

    if req.dry_run:
        safe_dump = req.model_dump()
        safe_dump.pop("credentials", None)
        return {"dry_run": True, "would_execute": safe_dump}

    result = client.call_with_transport(
        req.model,
        req.method,
        args=req.args or [],
        kwargs=req.kwargs or {},
    )

    return {"model": req.model, "method": req.method, "result": result}
