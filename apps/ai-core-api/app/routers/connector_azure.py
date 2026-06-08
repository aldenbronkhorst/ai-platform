"""Microsoft Admin connector — delegated Microsoft device auth + native admin tooling."""
import uuid
import time
from typing import Any
from pydantic import BaseModel, Field
from fastapi import APIRouter, Body, Depends, HTTPException
from app.core.security import DEVELOPER_ROLES, api_key_auth, require_role
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.token_storage import retrieve_token, store_token, delete_token
from app.services.connected_account_state import (
    mark_delegated_account_disconnected,
    record_delegated_diagnosis,
    sync_delegated_account_from_token,
    upsert_delegated_account,
)
from app.services.connector_commands import (
    AZURE_AUTHORITY_HOST,
    AZURE_CLI_CLIENT_ID,
    TENANT_ID,
    AZURE_TOKEN_ENDPOINT,
    diagnose_azure_connection,
    ensure_azure_cli_profile,
    extract_azure_username,
    microsoft_admin_device_scope_string,
    microsoft_admin_scope_profile,
    run_ms_admin_tool,
    run_azure_cli_command,
    validate_azure_cli_profile,
)

router = APIRouter(prefix="/connector/azure", tags=["Connector"])


class AzureCliRequest(BaseModel):
    command: str = Field(..., description="Azure CLI command")
    purpose: str = Field("", description="Purpose")
    timeout: int = Field(60, description="Timeout", le=300)


class MicrosoftAdminRequest(BaseModel):
    mode: str = Field(..., description="status, azure_cli, powershell, bicep, or graph_request")
    command: str = Field("", description="Command for azure_cli or bicep mode")
    script: str = Field("", description="PowerShell script for powershell mode")
    method: str = Field("GET", description="Graph HTTP method")
    path: str = Field("", description="Graph path for graph_request mode")
    api_version: str = Field("v1.0", description="Graph API version")
    body: Any = Field(None, description="Graph request body")
    headers: dict[str, Any] | None = Field(None, description="Additional Graph headers")
    purpose: str = Field("", description="Purpose")
    timeout: int = Field(60, description="Timeout", le=300)


@router.post("/cli")
async def azure_cli(req: AzureCliRequest, auth: dict = Depends(require_role(list(DEVELOPER_ROLES)))):
    """Execute an Azure CLI command as the connected user."""
    user_id = auth.get("user_id")
    return await run_azure_cli_command(req.command, user_id, timeout=req.timeout)


@router.post("/admin")
async def microsoft_admin(req: MicrosoftAdminRequest, auth: dict = Depends(require_role(list(DEVELOPER_ROLES)))):
    """Execute a Microsoft Admin connector operation as the connected user."""
    user_id = auth.get("user_id")
    return await run_ms_admin_tool(req.model_dump(exclude_none=True), user_id, timeout=req.timeout)


@router.post("/device-code")
async def start_device_code(req: dict | None = Body(default=None), auth: dict = Depends(api_key_auth)):
    """Start Azure OAuth device code flow for user-delegated auth."""
    request_id = uuid.uuid4().hex[:16]
    scope_profile = microsoft_admin_scope_profile((req or {}).get("scope_profile"))
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/devicecode",
                data={"client_id": AZURE_CLI_CLIENT_ID, "scope": microsoft_admin_device_scope_string(scope_profile)},
            )
        data = resp.json()
        if "error" in data:
            return {"status": "error", "error": data.get("error_description", data["error"]), "request_id": request_id}
        return {"status": "device_code_ready", "device_code": data["device_code"],
                "user_code": data["user_code"], "verification_uri": data["verification_uri"],
                "verification_url": data.get("verification_uri", "https://microsoft.com/devicelogin"),
                "interval": data.get("interval", 5), "expires_in": data.get("expires_in", 900),
                "scope_profile": scope_profile,
                "request_id": request_id}
    except Exception as e:
        return {"status": "error", "error": str(e), "request_id": request_id}


@router.post("/token-callback")
async def device_code_callback(
    req: dict,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Poll device code and store resulting token."""
    user_id = auth.get("user_id")
    device_code = req.get("device_code", "")
    scope_profile = microsoft_admin_scope_profile(req.get("scope_profile"))
    if not device_code or not user_id:
        raise HTTPException(status_code=400, detail="Missing device_code or auth")
    request_id = uuid.uuid4().hex[:16]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                      "client_id": AZURE_CLI_CLIENT_ID, "device_code": device_code,
                      "scope": microsoft_admin_device_scope_string(scope_profile),
                      "client_info": "1"},
            )
        data = resp.json()
        if "error" in data:
            pending_errors = {"authorization_pending", "slow_down"}
            return {
                "status": "pending" if data["error"] in pending_errors else "error",
                "error": data.get("error_description", data["error"]),
                "interval": 10 if data["error"] == "slow_down" else None,
                "request_id": request_id,
            }
        token_payload = {
            "client_id": AZURE_CLI_CLIENT_ID,
            "token_type": data.get("token_type"),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "scope": data.get("scope"),
            "id_token": data.get("id_token"),
            "client_info": data.get("client_info"),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        token_payload["username"] = extract_azure_username(token_payload)
        existing_token = await retrieve_token("azure", user_id) or {}
        token_payload["delegated_tokens"] = existing_token.get("delegated_tokens") or {}
        token_payload["consented_scope_profiles"] = sorted({
            *(existing_token.get("consented_scope_profiles") or []),
            scope_profile,
        })
        if scope_profile != "arm":
            delegated_tokens = dict(existing_token.get("delegated_tokens") or {})
            delegated_tokens[scope_profile] = {
                "token_type": data.get("token_type"),
                "access_token": data.get("access_token"),
                "scope": data.get("scope"),
                "expires_in": data.get("expires_in"),
                "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
            }
            merged_token = {
                **existing_token,
                "client_id": existing_token.get("client_id") or AZURE_CLI_CLIENT_ID,
                "refresh_token": data.get("refresh_token") or existing_token.get("refresh_token"),
                "delegated_tokens": delegated_tokens,
                "consented_scope_profiles": token_payload["consented_scope_profiles"],
                "username": existing_token.get("username") or token_payload["username"],
            }
            stored = await store_token("azure", user_id, merged_token)
            if not stored:
                return {"status": "error", "error": "key_vault_write_failed", "message": "Could not store credentials securely.", "request_id": request_id}
            await upsert_delegated_account(
                db,
                "azure",
                user_id,
                token_data=merged_token,
                status="connected",
                username=merged_token.get("username"),
                permission_summary=f"Microsoft {scope_profile} consent completed.",
                commit=True,
            )
            return {"status": "connected", "request_id": request_id, "scope_profile": scope_profile, "cli_profile_ready": bool(existing_token.get("access_token"))}

        stored = await store_token("azure", user_id, token_payload)
        if not stored:
            return {"status": "error", "error": "key_vault_write_failed", "message": "Could not store credentials securely.", "request_id": request_id}

        profile = await ensure_azure_cli_profile(user_id, token_payload)
        if not profile.get("ready"):
            await upsert_delegated_account(
                db,
                "azure",
                user_id,
                token_data=token_payload,
                status="error",
                username=token_payload.get("username"),
                permission_summary=profile.get("message", "Could not prepare Microsoft Admin profile."),
                commit=True,
            )
            return {
                "status": "error",
                "error": "azure_cli_profile_failed",
                "message": profile.get("message", "Could not prepare Microsoft Admin profile."),
                "request_id": request_id,
            }
        cli_check = await validate_azure_cli_profile(user_id)
        if not cli_check.get("ready"):
            await upsert_delegated_account(
                db,
                "azure",
                user_id,
                token_data=token_payload,
                status="error",
                username=token_payload.get("username"),
                permission_summary=cli_check.get("message", "Microsoft Admin profile validation failed."),
                commit=True,
            )
            return {
                "status": "error",
                "error": "azure_cli_profile_failed",
                "message": cli_check.get("message", "Microsoft Admin profile validation failed."),
                "request_id": request_id,
            }
        await upsert_delegated_account(
            db,
            "azure",
            user_id,
            token_data=token_payload,
            status="connected",
            username=token_payload.get("username"),
            permission_summary="Microsoft device authentication completed.",
            commit=True,
        )
        return {"status": "connected", "request_id": request_id, "cli_profile_ready": True}
    except Exception as e:
        return {"status": "error", "error": str(e), "request_id": request_id}


@router.get("/status")
async def azure_status(
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Check Azure connection status for the current user."""
    user_id = auth.get("user_id")
    return await sync_delegated_account_from_token(db, "azure", user_id, commit=True) if user_id else {"status": "not_connected"}


@router.post("/diagnose")
async def azure_diagnose(
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Validate the stored Azure delegated token without shelling out to az."""
    user_id = auth.get("user_id")
    result = await diagnose_azure_connection(user_id)
    if user_id:
        await record_delegated_diagnosis(db, "azure", user_id, result, commit=True)
    return result


@router.post("/disconnect")
async def azure_disconnect(
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Azure for the current user."""
    user_id = auth.get("user_id")
    if user_id:
        await delete_token("azure", user_id)
        await mark_delegated_account_disconnected(db, "azure", user_id, commit=True)
    return {"status": "disconnected"}
