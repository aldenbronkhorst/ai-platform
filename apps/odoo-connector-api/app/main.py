from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.security import internal_api_key_auth
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


@app.get("/")
async def root():
    return {"app": settings.app_name, "version": settings.app_version}
