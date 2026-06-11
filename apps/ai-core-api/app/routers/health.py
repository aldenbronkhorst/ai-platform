import asyncio
import os
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from app.core.database import get_db
from app.core.config import get_settings
from app.services.key_vault import get_secret_client

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("")
async def health_check():
    status_info = {
        "status": "healthy",
        "version": "0.1.0",
        "dependencies": {
            "postgresql": _configured("POSTGRES_HOST"),
            "key_vault": _configured("KEY_VAULT_URI"),
            "blob_storage": _configured("STORAGE_ACCOUNT_NAME"),
        },
    }

    config_issues = _startup_config_issues()
    if config_issues:
        if get_settings().app_env == "production":
            status_info["status"] = "degraded"
        status_info["config_issues"] = config_issues

    return status_info


@router.get("/dependencies")
async def dependency_health_check(db: AsyncSession = Depends(get_db)):
    return await _dependency_health_payload(db, deep=True)


@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    status_info = await _dependency_health_payload(db, deep=_deep_dependency_checks_enabled())
    status_code = 200 if status_info["status"] == "healthy" else 503
    return JSONResponse(content=status_info, status_code=status_code)


async def _dependency_health_payload(db: AsyncSession, deep: bool = False):
    status_info = {
        "status": "healthy",
        "version": "0.1.0",
        "dependencies": {}
    }

    try:
        await db.execute(text("SELECT 1"))
        status_info["dependencies"]["postgresql"] = "reachable"
    except Exception as exc:
        logger.warning("Health: PostgreSQL unreachable: %s", exc)
        status_info["dependencies"]["postgresql"] = "unreachable"

    kv_uri = os.environ.get("KEY_VAULT_URI")
    if kv_uri:
        status_info["dependencies"]["key_vault"] = await _check_key_vault(kv_uri, deep=deep)
    else:
        status_info["dependencies"]["key_vault"] = "not_configured"

    storage_name = os.environ.get("STORAGE_ACCOUNT_NAME")
    if storage_name:
        status_info["dependencies"]["blob_storage"] = await _check_blob_storage(storage_name, deep=deep)
    else:
        status_info["dependencies"]["blob_storage"] = "not_configured"

    config_issues = _startup_config_issues()
    if config_issues:
        if get_settings().app_env == "production":
            status_info["status"] = "degraded"
        status_info["config_issues"] = config_issues

    # /health/dependencies returns a deep diagnostic payload as informational HTTP 200.
    # /health/ready is shallow by default unless HEALTH_CHECK_DEEP is enabled.
    all_healthy = all(
        dep in ("reachable", "not_configured", "configured")
        for dep in status_info["dependencies"].values()
    )
    if not all_healthy:
        logger.warning("Health: dependencies degraded: %s", status_info["dependencies"])
        status_info["status"] = "degraded"

    return status_info


def _configured(*names: str) -> str:
    return "configured" if any(os.environ.get(name) for name in names) else "not_configured"


def _deep_dependency_checks_enabled() -> bool:
    explicit = os.environ.get("HEALTH_CHECK_DEEP")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return False


async def _run_dependency_check(name: str, check, deep: bool = False):
    if not (deep or _deep_dependency_checks_enabled()):
        return "configured"
    try:
        await asyncio.to_thread(check)
        return "reachable"
    except Exception as exc:
        logger.warning("Health: %s unreachable: %s", name, exc)
        return "unreachable"


async def _check_key_vault(kv_uri: str, deep: bool = False) -> str:
    def check():
        client = get_secret_client(kv_uri)
        if not client:
            raise RuntimeError("Key Vault is not configured")
        client.get_secret("api-key")

    return await _run_dependency_check("Key Vault", check, deep=deep)


async def _check_blob_storage(storage_name: str, deep: bool = False) -> str:
    def check():
        credential = DefaultAzureCredential()
        blob_client = BlobServiceClient(
            account_url=f"https://{storage_name}.blob.core.windows.net",
            credential=credential
        )
        next(blob_client.list_containers(), None)

    return await _run_dependency_check("Blob storage", check, deep=deep)


def _validate_startup_config() -> list:
    """Validates critical configuration on startup.

    Returns a list of issue dicts. An empty list means all checks passed.
    """
    issues = []
    settings = get_settings()

    # APP_ENV should never be None
    if settings.app_env == "production" and settings.debug:
        issues.append({
            "check": "DEBUG",
            "status": "FAIL",
            "message": "DEBUG=true is not allowed in production. Set DEBUG=false.",
        })

    # ODOO_CONNECTOR_URL
    connector_url = os.environ.get("ODOO_CONNECTOR_URL", "")
    if not connector_url:
        issues.append({
            "check": "ODOO_CONNECTOR_URL",
            "status": "FAIL",
            "message": "ODOO_CONNECTOR_URL is not configured. Odoo integration will not work.",
        })

    # ODOO_CONNECTOR_API_KEY
    connector_key = os.environ.get("ODOO_CONNECTOR_API_KEY", "")
    if not connector_key:
        issues.append({
            "check": "ODOO_CONNECTOR_API_KEY",
            "status": "FAIL",
            "message": "ODOO_CONNECTOR_API_KEY is not configured.",
        })

    # KEY_VAULT_URI
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if not kv_uri:
        issues.append({
            "check": "KEY_VAULT_URI",
            "status": "FAIL",
            "message": "KEY_VAULT_URI is not configured. Credential storage will not work.",
        })

    # DB config check (basic presence)
    if not os.environ.get("POSTGRES_HOST"):
        issues.append({
            "check": "POSTGRES_HOST",
            "status": "FAIL",
            "message": "POSTGRES_HOST is not configured.",
        })

    return issues


def _startup_config_issues() -> list:
    try:
        return _validate_startup_config()
    except Exception as exc:
        logger.error("Health: config validation failed: %s", exc)
        return []
