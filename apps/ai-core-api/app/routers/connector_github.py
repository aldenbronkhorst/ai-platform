"""GitHub connector — user-delegated OAuth flow + gh CLI execution."""
import logging
import uuid
import os
import time
import jwt
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from app.core.config import get_settings
from app.core.security import DEVELOPER_ROLES, api_key_auth, require_role
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.key_vault import get_secret_value
from app.services.token_storage import store_token, delete_token
from app.services.connected_account_state import (
    mark_delegated_account_disconnected,
    record_delegated_diagnosis,
    sync_delegated_account_from_token,
    upsert_delegated_account,
)
from app.services.connectors.github_cli import diagnose_github_connection, run_github_cli_command

router = APIRouter(prefix="/connector/github", tags=["Connector"])
logger = logging.getLogger(__name__)

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI", "https://ai.lotslotsmore.com/settings/connections")
GITHUB_CLIENT_ID_SECRET_NAME = os.environ.get("GITHUB_CLIENT_ID_SECRET_NAME", "github-oauth-client-id")
GITHUB_CLIENT_SECRET_SECRET_NAME = os.environ.get("GITHUB_CLIENT_SECRET_SECRET_NAME", "github-oauth-client-secret")
OAUTH_STATE_TTL_SECONDS = 10 * 60


def _is_configured_value(value: str) -> bool:
    normalized = (value or "").strip().lower()
    return bool(normalized and not normalized.startswith("your-") and normalized not in {"placeholder", "changeme", "todo"})


class GithubCliRequest(BaseModel):
    command: str = Field(..., description="GitHub CLI command (gh, git, rg, jq)")
    purpose: str = Field("", description="Purpose")
    timeout: int = Field(60, description="Timeout", le=300)


@router.post("/cli")
async def github_cli(req: GithubCliRequest, auth: dict = Depends(require_role(list(DEVELOPER_ROLES)))):
    """Execute a GitHub CLI command as the connected user."""
    return await run_github_cli_command(req.command, auth.get("user_id"), timeout=req.timeout)


async def _resolve_secret_config(env_value: str, secret_name: str) -> str:
    if _is_configured_value(env_value):
        return env_value.strip()
    try:
        value = await get_secret_value(secret_name)
    except Exception:
        logger.warning("Could not resolve GitHub OAuth config secret.")
        return ""
    return value.strip() if _is_configured_value(value) else ""


async def _github_oauth_config() -> tuple[str, str]:
    client_id = await _resolve_secret_config(GITHUB_CLIENT_ID, GITHUB_CLIENT_ID_SECRET_NAME)
    client_secret = await _resolve_secret_config(GITHUB_CLIENT_SECRET, GITHUB_CLIENT_SECRET_SECRET_NAME)
    return client_id, client_secret


def _oauth_state_signing_key(client_secret: str) -> bytes:
    settings = get_settings()
    key = client_secret or settings.api_key
    if not key:
        raise HTTPException(status_code=500, detail="GitHub OAuth state signing is not configured.")
    return key.encode("utf-8")


def _sign_state_payload(payload: dict, client_secret: str) -> str:
    return jwt.encode(payload, _oauth_state_signing_key(client_secret), algorithm="HS256")


def _verify_state_payload(state: str, client_secret: str, user_id: object) -> None:
    try:
        payload = jwt.decode(
            state,
            _oauth_state_signing_key(client_secret),
            algorithms=["HS256"],
            options={"require": ["exp", "nonce", "user_id"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="GitHub OAuth state has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid GitHub OAuth state.")

    if str(payload.get("user_id")) != str(user_id):
        raise HTTPException(status_code=400, detail="GitHub OAuth state does not match the current user.")


@router.get("/auth-url")
async def github_auth_url(auth: dict = Depends(api_key_auth)):
    """Return GitHub OAuth authorization URL for the current user."""
    client_id, client_secret = await _github_oauth_config()
    if not _is_configured_value(client_id):
        return {"status": "not_configured", "message": "GitHub OAuth client ID not configured."}
    if not _is_configured_value(client_secret) and not _is_configured_value(get_settings().api_key):
        return {"status": "not_configured", "message": "GitHub OAuth state signing is not configured."}
    state = _sign_state_payload(
        {
            "user_id": str(auth.get("user_id")),
            "nonce": uuid.uuid4().hex,
            "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS,
        },
        client_secret,
    )
    url = (f"https://github.com/login/oauth/authorize?client_id={client_id}"
           f"&redirect_uri={GITHUB_REDIRECT_URI}&state={state}&scope=repo,workflow,read:org,admin:repo_hook")
    return {"status": "ready", "auth_url": url, "state": state}


@router.post("/oauth-callback")
async def github_oauth_callback(
    req: dict,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Handle GitHub OAuth callback: exchange code for token, store in KV."""
    user_id = auth.get("user_id")
    code = req.get("code", "")
    state = req.get("state", "")
    if not code or not user_id:
        raise HTTPException(status_code=400, detail="Missing code or configuration")
    client_id, client_secret = await _github_oauth_config()
    if not _is_configured_value(client_id) or not _is_configured_value(client_secret):
        raise HTTPException(status_code=400, detail="GitHub OAuth is not configured.")
    if not state:
        raise HTTPException(status_code=400, detail="Missing GitHub OAuth state.")
    _verify_state_payload(state, client_secret, user_id)
    request_id = uuid.uuid4().hex[:16]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={"client_id": client_id, "client_secret": client_secret,
                      "code": code, "redirect_uri": GITHUB_REDIRECT_URI},
                headers={"Accept": "application/json"},
            )
        data = resp.json()
        if "error" in data:
            return {"status": "error", "error": data.get("error_description", data["error"]), "request_id": request_id}
        access_token = data.get("access_token")
        if not access_token:
            return {"status": "error", "error": "No access_token in response", "request_id": request_id}
        token_payload = {
            "access_token": access_token,
            "token_type": data.get("token_type", "bearer"),
            "scope": data.get("scope", ""),
        }
        stored = await store_token("github", user_id, token_payload)
        if not stored:
            return {"status": "error", "error": "key_vault_write_failed", "message": "Could not store credentials securely.", "request_id": request_id}
        diagnosis = await diagnose_github_connection(user_id)
        if diagnosis.get("status") != "success":
            await upsert_delegated_account(
                db,
                "github",
                user_id,
                token_data=token_payload,
                status="error",
                username=diagnosis.get("login"),
                permission_summary=diagnosis.get("message", "GitHub CLI profile validation failed."),
                commit=True,
            )
            return {
                "status": "error",
                "error": "github_cli_profile_failed",
                "message": diagnosis.get("message", "GitHub CLI profile validation failed."),
                "request_id": request_id,
            }
        await upsert_delegated_account(
            db,
            "github",
            user_id,
            token_data=token_payload,
            status="connected",
            username=diagnosis.get("login"),
            permission_summary=diagnosis.get("message", "GitHub OAuth authentication completed."),
            commit=True,
        )
        return {"status": "connected", "request_id": request_id}
    except Exception as e:
        return {"status": "error", "error": str(e), "request_id": request_id}


@router.get("/status")
async def github_status(
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Check GitHub connection status for the current user."""
    user_id = auth.get("user_id")
    return await sync_delegated_account_from_token(db, "github", user_id, commit=True) if user_id else {"status": "not_connected"}


@router.post("/diagnose")
async def github_diagnose(
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Validate the stored GitHub delegated token without running gh."""
    user_id = auth.get("user_id")
    result = await diagnose_github_connection(user_id)
    if user_id:
        await record_delegated_diagnosis(db, "github", user_id, result, commit=True)
    return result


@router.post("/disconnect")
async def github_disconnect(
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect GitHub for the current user."""
    user_id = auth.get("user_id")
    if user_id:
        await delete_token("github", user_id)
        await mark_delegated_account_disconnected(db, "github", user_id, commit=True)
    return {"status": "disconnected"}
