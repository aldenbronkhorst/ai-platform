"""Azure connector — user-delegated OAuth device code flow + az CLI execution."""
import logging
import uuid
import os
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import api_key_auth
from app.services.token_storage import store_token, retrieve_token, delete_token, token_status
from app.services.connector_commands import diagnose_azure_connection, run_azure_cli_command

router = APIRouter(prefix="/connector/azure", tags=["Connector"])
logger = logging.getLogger(__name__)

AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "fcefb508-bb9d-4d5d-b1c5-6d2ef04c0208")
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")


class AzureCliRequest(BaseModel):
    command: str = Field(..., description="Azure CLI command")
    purpose: str = Field("", description="Purpose")
    timeout: int = Field(60, description="Timeout", le=300)


@router.post("/cli")
async def azure_cli(req: AzureCliRequest, auth: dict = Depends(api_key_auth)):
    """Execute an Azure CLI command as the connected user."""
    user_id = auth.get("user_id")
    return await run_azure_cli_command(req.command, user_id, timeout=req.timeout)


@router.post("/device-code")
async def start_device_code(auth: dict = Depends(api_key_auth)):
    """Start Azure OAuth device code flow for user-delegated auth."""
    request_id = uuid.uuid4().hex[:16]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/devicecode",
                data={"client_id": AZURE_CLIENT_ID, "scope": "https://management.azure.com/user_impersonation offline_access openid profile"},
            )
        data = resp.json()
        if "error" in data:
            return {"status": "error", "error": data.get("error_description", data["error"]), "request_id": request_id}
        return {"status": "device_code_ready", "device_code": data["device_code"],
                "user_code": data["user_code"], "verification_uri": data["verification_uri"],
                "verification_url": data.get("verification_uri", "https://microsoft.com/devicelogin"),
                "interval": data.get("interval", 5), "expires_in": data.get("expires_in", 900),
                "request_id": request_id}
    except Exception as e:
        return {"status": "error", "error": str(e), "request_id": request_id}


@router.post("/token-callback")
async def device_code_callback(req: dict, auth: dict = Depends(api_key_auth)):
    """Poll device code and store resulting token."""
    user_id = auth.get("user_id")
    device_code = req.get("device_code", "")
    if not device_code or not user_id:
        raise HTTPException(status_code=400, detail="Missing device_code or auth")
    request_id = uuid.uuid4().hex[:16]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
                data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                      "client_id": AZURE_CLIENT_ID, "device_code": device_code},
            )
        data = resp.json()
        if "error" in data:
            return {"status": "pending" if data["error"] == "authorization_pending" else "error",
                    "error": data.get("error_description", data["error"]), "request_id": request_id}
        stored = await store_token("azure", user_id, {
            "token_type": data.get("token_type"),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "scope": data.get("scope"),
            "expires_in": data.get("expires_in"),
        })
        if not stored:
            return {"status": "error", "error": "key_vault_write_failed", "message": "Could not store credentials securely.", "request_id": request_id}
        return {"status": "connected", "request_id": request_id}
    except Exception as e:
        return {"status": "error", "error": str(e), "request_id": request_id}


@router.get("/status")
async def azure_status(auth: dict = Depends(api_key_auth)):
    """Check Azure connection status for the current user."""
    user_id = auth.get("user_id")
    return await token_status("azure", user_id) if user_id else {"status": "not_connected"}


@router.post("/diagnose")
async def azure_diagnose(auth: dict = Depends(api_key_auth)):
    """Validate the stored Azure delegated token without shelling out to az."""
    return await diagnose_azure_connection(auth.get("user_id"))


@router.post("/disconnect")
async def azure_disconnect(auth: dict = Depends(api_key_auth)):
    """Disconnect Azure for the current user."""
    user_id = auth.get("user_id")
    if user_id:
        await delete_token("azure", user_id)
    return {"status": "disconnected"}
