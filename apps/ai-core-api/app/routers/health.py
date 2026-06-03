import asyncio
import os
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
from azure.servicebus.management import ServiceBusAdministrationClient

from app.core.database import get_db
from app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    status_info = {
        "status": "healthy",
        "version": "0.1.0",
        "dependencies": {}
    }

    try:
        result = await db.execute(text("SELECT 1"))
        status_info["dependencies"]["postgresql"] = "reachable"
    except Exception as exc:
        logger.warning("Health: PostgreSQL unreachable: %s", exc)
        status_info["dependencies"]["postgresql"] = "unreachable"

    kv_uri = os.environ.get("KEY_VAULT_URI")
    if kv_uri:
        status_info["dependencies"]["key_vault"] = await _check_key_vault(kv_uri)
    else:
        status_info["dependencies"]["key_vault"] = "not_configured"

    storage_name = os.environ.get("STORAGE_ACCOUNT_NAME")
    if storage_name:
        status_info["dependencies"]["blob_storage"] = await _check_blob_storage(storage_name)
    else:
        status_info["dependencies"]["blob_storage"] = "not_configured"

    sb_namespace = os.environ.get("AZURE_SERVICE_BUS_NAMESPACE") or os.environ.get("SERVICE_BUS_NAMESPACE")
    if sb_namespace:
        queue_name = os.environ.get("AZURE_SERVICE_BUS_QUEUE_NAME", "ai-jobs")
        status_info["dependencies"]["service_bus"] = await _check_service_bus(sb_namespace, queue_name)
    else:
        status_info["dependencies"]["service_bus"] = "not_configured"

    # Startup config validation (degrades status in production, warns in development)
    try:
        config_issues = _validate_startup_config()
        is_production = get_settings().app_env == "production"
        if config_issues:
            if is_production:
                status_info["status"] = "degraded"
            status_info["config_issues"] = config_issues
    except Exception as exc:
        logger.error("Health: config validation failed: %s", exc)
        config_issues = []

    # Always return 200 so Container App liveness/readiness probes never fail.
    # Dependency issues are reported in the response body as informational.
    all_healthy = all(
        dep in ("reachable", "not_configured", "configured")
        for dep in status_info["dependencies"].values()
    )
    if not all_healthy:
        logger.warning("Health: dependencies degraded: %s", status_info["dependencies"])
        status_info["status"] = "degraded"

    return status_info


def _deep_dependency_checks_enabled() -> bool:
    explicit = os.environ.get("HEALTH_CHECK_DEEP")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return get_settings().app_env == "production"


async def _run_dependency_check(name: str, check):
    if not _deep_dependency_checks_enabled():
        return "configured"
    try:
        await asyncio.to_thread(check)
        return "reachable"
    except Exception as exc:
        logger.warning("Health: %s unreachable: %s", name, exc)
        return "unreachable"


async def _check_key_vault(kv_uri: str) -> str:
    def check():
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=kv_uri, credential=credential)
        client.get_secret("api-key")

    return await _run_dependency_check("Key Vault", check)


async def _check_blob_storage(storage_name: str) -> str:
    def check():
        credential = DefaultAzureCredential()
        blob_client = BlobServiceClient(
            account_url=f"https://{storage_name}.blob.core.windows.net",
            credential=credential
        )
        next(blob_client.list_containers(), None)

    return await _run_dependency_check("Blob storage", check)


async def _check_service_bus(sb_namespace: str, queue_name: str) -> str:
    def check():
        credential = DefaultAzureCredential()
        client = ServiceBusAdministrationClient(
            fully_qualified_namespace=f"{sb_namespace}.servicebus.windows.net",
            credential=credential,
        )
        try:
            client.get_queue_runtime_properties(queue_name)
        finally:
            client.close()

    return await _run_dependency_check("Service Bus", check)


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
