from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate
import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
from azure.servicebus import ServiceBusClient
import psycopg2

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    status_info = {
        "status": "healthy",
        "version": "0.1.0",
        "dependencies": {}
    }

    # Check PostgreSQL
    try:
        result = await db.execute(__import__('sqlalchemy').text("SELECT 1"))
        status_info["dependencies"]["postgresql"] = "reachable"
    except Exception as e:
        status_info["dependencies"]["postgresql"] = f"error: {str(e)}"

    # Check Key Vault
    try:
        kv_uri = os.environ.get("KEY_VAULT_URI")
        if kv_uri:
            credential = DefaultAzureCredential()
            SecretClient(vault_url=kv_uri, credential=credential)
            status_info["dependencies"]["key_vault"] = "reachable"
        else:
            status_info["dependencies"]["key_vault"] = "not_configured"
    except Exception as e:
        status_info["dependencies"]["key_vault"] = f"error: {str(e)}"

    # Check Blob Storage
    try:
        storage_name = os.environ.get("STORAGE_ACCOUNT_NAME")
        if storage_name:
            credential = DefaultAzureCredential()
            blob_client = BlobServiceClient(
                account_url=f"https://{storage_name}.blob.core.windows.net",
                credential=credential
            )
            next(blob_client.list_containers(), None)
            status_info["dependencies"]["blob_storage"] = "reachable"
        else:
            status_info["dependencies"]["blob_storage"] = "not_configured"
    except Exception as e:
        status_info["dependencies"]["blob_storage"] = f"error: {str(e)}"

    # Check Service Bus
    try:
        sb_namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
        if sb_namespace:
            credential = DefaultAzureCredential()
            sb_client = ServiceBusClient(
                fully_qualified_namespace=f"{sb_namespace}.servicebus.windows.net",
                credential=credential
            )
            sb_client.close()
            status_info["dependencies"]["service_bus"] = "reachable"
        else:
            status_info["dependencies"]["service_bus"] = "not_configured"
    except Exception as e:
        status_info["dependencies"]["service_bus"] = f"error: {str(e)}"

    all_healthy = all(
        dep == "reachable" or dep == "not_configured"
        for dep in status_info["dependencies"].values()
    )
    if not all_healthy:
        status_info["status"] = "degraded"

    return status_info
