from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
import os
import psycopg2
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
from azure.servicebus import ServiceBusClient
import logging
from opencensus.ext.azure.log_exporter import AzureLogHandler

app = FastAPI(title="AI Platform Core API", version="0.1.0")

# Configure Application Insights logging if connection string is available
appinsights_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if appinsights_conn_str:
    logger = logging.getLogger(__name__)
    logger.addHandler(AzureLogHandler(connection_string=appinsights_conn_str))
    logger.setLevel(logging.INFO)
else:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)


def get_azure_credential():
    return DefaultAzureCredential()


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint. Returns service status and dependency connectivity."""
    status_info = {
        "status": "healthy",
        "version": "0.1.0",
        "dependencies": {}
    }

    # Check Key Vault connectivity
    try:
        kv_uri = os.environ.get("KEY_VAULT_URI")
        if kv_uri:
            credential = get_azure_credential()
            SecretClient(vault_url=kv_uri, credential=credential)
            status_info["dependencies"]["key_vault"] = "reachable"
        else:
            status_info["dependencies"]["key_vault"] = "not_configured"
    except Exception as e:
        status_info["dependencies"]["key_vault"] = f"error: {str(e)}"

    # Check PostgreSQL connectivity
    try:
        pg_host = os.environ.get("POSTGRES_HOST")
        pg_db = os.environ.get("POSTGRES_DB")
        pg_user = os.environ.get("POSTGRES_USER")
        pg_password = os.environ.get("POSTGRES_PASSWORD")
        if all([pg_host, pg_db, pg_user, pg_password]):
            conn = psycopg2.connect(
                host=pg_host,
                database=pg_db,
                user=pg_user,
                password=pg_password,
                port=5432,
                connect_timeout=5
            )
            conn.close()
            status_info["dependencies"]["postgresql"] = "reachable"
        else:
            status_info["dependencies"]["postgresql"] = "not_configured"
    except Exception as e:
        status_info["dependencies"]["postgresql"] = f"error: {str(e)}"

    # Check Storage connectivity
    try:
        storage_name = os.environ.get("STORAGE_ACCOUNT_NAME")
        if storage_name:
            credential = get_azure_credential()
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

    # Check Service Bus connectivity
    try:
        sb_namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
        if sb_namespace:
            credential = get_azure_credential()
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

    # Overall status
    all_healthy = all(
        dep == "reachable" or dep == "not_configured"
        for dep in status_info["dependencies"].values()
    )
    if not all_healthy:
        status_info["status"] = "degraded"

    return JSONResponse(content=status_info)


@app.get("/", tags=["root"])
async def root():
    return {
        "name": "AI Platform Core API",
        "version": "0.1.0",
        "docs": "/docs"
    }
