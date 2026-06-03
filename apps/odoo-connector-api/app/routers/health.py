import os
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.security import internal_api_key_auth
from app.models.schemas import CapabilitiesResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health_check():
    settings = get_settings()
    config_issues = _validate_startup_config()

    response = {
        "status": "healthy" if not config_issues else "degraded",
        "version": settings.app_version,
        "capabilities": [
            "odoo.ops.run",
        ],
    }
    if config_issues:
        response["config_issues"] = config_issues

    status_code = 200 if not config_issues else 503
    return JSONResponse(content=response, status_code=status_code)


def _validate_startup_config() -> list:
    """Validates critical configuration on startup."""
    issues = []
    settings = get_settings()

    if settings.app_env == "production" and settings.debug:
        issues.append({
            "check": "DEBUG",
            "status": "FAIL",
            "message": "DEBUG=true is not allowed in production. Set DEBUG=false.",
        })

    if not settings.internal_api_key:
        issues.append({
            "check": "INTERNAL_API_KEY",
            "status": "FAIL",
            "message": "INTERNAL_API_KEY is not configured. Internal auth will reject all requests.",
        })

    return issues


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities(_auth: dict = Depends(internal_api_key_auth)):
    return CapabilitiesResponse(
        endpoints=[
            {"path": "/odoo/ops/run", "method": "POST", "description": "Run consolidated Odoo operations by mode"},
        ],
        execute_kw_enabled=True,
        execute_kw_write_methods=True,
    )
