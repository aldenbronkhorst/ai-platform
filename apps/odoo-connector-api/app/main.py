import logging
import xmlrpc.client
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.odoo_client import OdooError, OdooAuthError
from app.core.middleware import CorrelationIdMiddleware
from app.routers import health, schema, records, execute_kw, attachments, messages, reports

settings = get_settings()

# Application Insights telemetry
if settings.appinsights_connection_string:
    from opencensus.ext.azure.log_exporter import AzureLogHandler
    from opencensus.ext.azure.trace_exporter import AzureExporter
    from opencensus.trace.samplers import ProbabilitySampler
    from opencensus.trace.tracer import Tracer
    from opencensus.trace.span import SpanKind

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.addHandler(AzureLogHandler(connection_string=settings.appinsights_connection_string))

    tracer = Tracer(
        exporter=AzureExporter(connection_string=settings.appinsights_connection_string),
        sampler=ProbabilitySampler(1.0),
    )
else:
    tracer = None

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Internal HTTP API for Odoo integration. Not an MCP server.",
)

app.add_middleware(CorrelationIdMiddleware)


@app.middleware("http")
async def appinsights_middleware(request: Request, call_next):
    if tracer:
        with tracer.span(name=f"{request.method} {request.url.path}") as span:
            span.span_kind = SpanKind.SERVER
            span.add_attribute("http.method", request.method)
            span.add_attribute("http.path", request.url.path)
            span.add_attribute("http.target", str(request.url))
            response = await call_next(request)
            span.add_attribute("http.status_code", response.status_code)
            return response
    else:
        return await call_next(request)


app.include_router(health.router, tags=["Health"])
app.include_router(schema.router, prefix="/schema", tags=["Schema"])
app.include_router(records.router, prefix="/records", tags=["Records"])
app.include_router(execute_kw.router, prefix="/execute-kw", tags=["Execute"])
app.include_router(attachments.router, prefix="/attachments", tags=["Attachments"])
app.include_router(messages.router, prefix="/messages", tags=["Messages"])
app.include_router(reports.router, prefix="/reports", tags=["Reports"])


@app.exception_handler(OdooAuthError)
async def odoo_auth_error_handler(request: Request, exc: OdooAuthError):
    return JSONResponse(
        status_code=401,
        content={
            "error": "odoo_auth_failed",
            "message": str(exc),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.exception_handler(OdooError)
async def odoo_error_handler(request: Request, exc: OdooError):
    return JSONResponse(
        status_code=400,
        content={
            "error": "odoo_error",
            "message": str(exc),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.exception_handler(xmlrpc.client.ProtocolError)
async def xmlrpc_protocol_error_handler(request: Request, exc: xmlrpc.client.ProtocolError):
    return JSONResponse(
        status_code=502,
        content={
            "error": "odoo_transport_error",
            "message": f"XML-RPC protocol error: {exc.errcode} {exc.errmsg}",
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.getLogger(__name__).exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An internal error occurred.",
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.get("/")
async def root():
    return {"app": settings.app_name, "version": settings.app_version}
