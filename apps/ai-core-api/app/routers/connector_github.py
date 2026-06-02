"""GitHub connector — user-delegated OAuth flow + gh CLI execution."""
import logging
import uuid
import os
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import api_key_auth
from app.services.ops_command_runner import run_command
from app.services.token_storage import store_token, retrieve_token, delete_token, token_status

router = APIRouter(prefix="/connector/github", tags=["Connector"])
logger = logging.getLogger(__name__)

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI", "https://ai.lotslotsmore.com/settings/connections")


class GithubCliRequest(BaseModel):
    command: str = Field(..., description="GitHub CLI command (gh, git, rg, jq)")
    purpose: str = Field("", description="Purpose")
    timeout: int = Field(60, description="Timeout", le=300)


@router.post("/cli")
async def github_cli(req: GithubCliRequest, auth: dict = Depends(api_key_auth)):
    """Execute a GitHub CLI command as the connected user."""
    command = req.command.strip()
    request_id = uuid.uuid4().hex[:16]
    result = await run_command(command, timeout=req.timeout)
    output = result.to_dict()
    output.update({"command": command, "connector": "github_cli", "request_id": request_id,
                    "status": "success" if result.success else "failed"})
    return output


@router.get("/auth-url")
async def github_auth_url(auth: dict = Depends(api_key_auth)):
    """Return GitHub OAuth authorization URL for the current user."""
    if not GITHUB_CLIENT_ID:
        return {"status": "not_configured", "message": "GitHub OAuth client ID not configured."}
    state = uuid.uuid4().hex[:16]
    url = (f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}"
           f"&redirect_uri={GITHUB_REDIRECT_URI}&state={state}&scope=repo,workflow,read:org,admin:repo_hook")
    return {"status": "ready", "auth_url": url, "state": state}


@router.post("/oauth-callback")
async def github_oauth_callback(req: dict, auth: dict = Depends(api_key_auth)):
    """Handle GitHub OAuth callback: exchange code for token, store in KV."""
    user_id = auth.get("user_id")
    code = req.get("code", "")
    if not code or not user_id or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="Missing code or configuration")
    request_id = uuid.uuid4().hex[:16]
    try:
        import httpx
        resp = httpx.post("https://github.com/login/oauth/access_token",
                          data={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET,
                                "code": code, "redirect_uri": GITHUB_REDIRECT_URI},
                          headers={"Accept": "application/json"}, timeout=30)
        data = resp.json()
        if "error" in data:
            return {"status": "error", "error": data.get("error_description", data["error"]), "request_id": request_id}
        access_token = data.get("access_token")
        if not access_token:
            return {"status": "error", "error": "No access_token in response", "request_id": request_id}
        stored = await store_token("github", user_id, {
            "access_token": access_token,
            "token_type": data.get("token_type", "bearer"),
            "scope": data.get("scope", ""),
        })
        if not stored:
            return {"status": "error", "error": "key_vault_write_failed", "message": "Could not store credentials securely.", "request_id": request_id}
        return {"status": "connected", "request_id": request_id}
    except Exception as e:
        return {"status": "error", "error": str(e), "request_id": request_id}


@router.get("/status")
async def github_status(auth: dict = Depends(api_key_auth)):
    """Check GitHub connection status for the current user."""
    user_id = auth.get("user_id")
    return await token_status("github", user_id) if user_id else {"status": "not_connected"}


@router.post("/disconnect")
async def github_disconnect(auth: dict = Depends(api_key_auth)):
    """Disconnect GitHub for the current user."""
    user_id = auth.get("user_id")
    if user_id:
        await delete_token("github", user_id)
    return {"status": "disconnected"}
