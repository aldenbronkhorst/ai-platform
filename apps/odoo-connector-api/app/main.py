import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.errors import classify_odoo_error
from app.core.odoo_client import OdooError
from app.core.middleware import CorrelationIdMiddleware
from app.routers import health
from app.routers import guidance as guidance_router
from app.routers import orm_runner as orm_runner_router

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
app.include_router(guidance_router.router, prefix="/odoo", tags=["Odoo"])
app.include_router(orm_runner_router.router, prefix="/odoo/orm", tags=["Odoo"])


@app.exception_handler(OdooError)
async def odoo_error_handler(request: Request, exc: OdooError):
    # Fallback for any OdooError (incl. OdooAuthError) that bubbles up outside the
    # run endpoint. The run endpoint classifies inline via the same function, so a
    # failed Odoo call looks the same whichever path handles it.
    status_code, content = classify_odoo_error(exc)
    content["correlation_id"] = getattr(request.state, "correlation_id", None)
    return JSONResponse(status_code=status_code, content=content)


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
