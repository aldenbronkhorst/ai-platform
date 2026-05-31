from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
from azure.servicebus import ServiceBusClient

router = APIRouter(prefix="/health", tags=["health"])


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
    except Exception:
        status_info["dependencies"]["postgresql"] = "unreachable"

    try:
        kv_uri = os.environ.get("KEY_VAULT_URI")
        if kv_uri:
            credential = DefaultAzureCredential()
            SecretClient(vault_url=kv_uri, credential=credential)
            status_info["dependencies"]["key_vault"] = "reachable"
        else:
            status_info["dependencies"]["key_vault"] = "not_configured"
    except Exception:
        status_info["dependencies"]["key_vault"] = "unreachable"

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
    except Exception:
        status_info["dependencies"]["blob_storage"] = "unreachable"

    try:
        sb_namespace = os.environ.get("AZURE_SERVICE_BUS_NAMESPACE") or os.environ.get("SERVICE_BUS_NAMESPACE")
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
    except Exception:
        status_info["dependencies"]["service_bus"] = "unreachable"

    all_healthy = all(
        dep in ("reachable", "not_configured")
        for dep in status_info["dependencies"].values()
    )
    if not all_healthy:
        status_info["status"] = "degraded"
        return JSONResponse(status_code=503, content=status_info)

    return status_info
