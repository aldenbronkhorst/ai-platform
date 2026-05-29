import xmlrpc.client
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.odoo_client import OdooError, OdooAuthError
from app.routers import health, schema, records, execute_kw, attachments, messages

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Internal HTTP API for Odoo integration. Not an MCP server.",
)

app.include_router(health.router, tags=["Health"])
app.include_router(schema.router, prefix="/schema", tags=["Schema"])
app.include_router(records.router, prefix="/records", tags=["Records"])
app.include_router(execute_kw.router, prefix="/execute-kw", tags=["Execute"])
app.include_router(attachments.router, prefix="/attachments", tags=["Attachments"])
app.include_router(messages.router, prefix="/messages", tags=["Messages"])


@app.exception_handler(OdooAuthError)
async def odoo_auth_error_handler(request: Request, exc: OdooAuthError):
    return JSONResponse(
        status_code=401,
        content={"error": "odoo_auth_failed", "message": str(exc)},
    )


@app.exception_handler(OdooError)
async def odoo_error_handler(request: Request, exc: OdooError):
    return JSONResponse(
        status_code=400,
        content={"error": "odoo_error", "message": str(exc)},
    )


@app.exception_handler(xmlrpc.client.ProtocolError)
async def xmlrpc_protocol_error_handler(request: Request, exc: xmlrpc.client.ProtocolError):
    return JSONResponse(
        status_code=502,
        content={"error": "odoo_transport_error", "message": f"XML-RPC protocol error: {exc}"},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": str(exc)},
    )


@app.get("/")
async def root():
    return {"app": settings.app_name, "version": settings.app_version}
