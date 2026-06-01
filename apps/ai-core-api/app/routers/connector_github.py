"""GitHub CLI connector — OAuth flow and native gh/git commands with diagnostics."""
import logging
import uuid
import os
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import api_key_auth
from app.services.ops_command_runner import run_command

router = APIRouter(prefix="/connector/github", tags=["Connector"])
logger = logging.getLogger(__name__)

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI", "https://ai.lotslotsmore.com/settings/connections")


class GithubCliRequest(BaseModel):
    command: str = Field(..., description="GitHub CLI command (gh, git, rg, jq)")
    purpose: str = Field("", description="Why this command is needed")
    timeout: int = Field(60, description="Command timeout in seconds", le=300)


@router.post("/cli")
async def github_cli(req: GithubCliRequest, auth: dict = Depends(api_key_auth)):
    """Execute a GitHub CLI command."""
    command = req.command.strip()
    request_id = uuid.uuid4().hex[:16]
    result = await run_command(command, timeout=req.timeout)
    output = result.to_dict()
    output["command"] = command
    output["purpose"] = req.purpose
    output["connector"] = "github_cli"
    output["request_id"] = request_id
    output["status"] = "success" if result.success else "failed"
    if not result.success:
        logger.warning("GitHub CLI failed | exit=%d error=%s", result.exit_code, result.error or result.stderr[:200])
    return output


@router.get("/auth-url")
async def github_auth_url(auth: dict = Depends(api_key_auth)):
    """Return GitHub OAuth app authorization URL."""
    if not GITHUB_CLIENT_ID:
        return {"status": "not_configured", "message": "GitHub OAuth client ID not configured."}
    url = f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&redirect_uri={GITHUB_REDIRECT_URI}&scope=repo,workflow,admin:org,read:org,admin:repo_hook"
    return {"status": "ready", "auth_url": url, "client_id": GITHUB_CLIENT_ID}


@router.post("/diagnose")
async def github_diagnose(auth: dict = Depends(api_key_auth)):
    """Run GitHub CLI diagnostics."""
    request_id = uuid.uuid4().hex[:16]
    commands = [
        "gh --version",
        "gh auth status",
    ]
    results = []
    for cmd in commands:
        result = await run_command(cmd, timeout=30)
        results.append({
            "command": cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout[:5000] if result.stdout else "",
            "stderr": result.stderr[:2000] if result.stderr else "",
            "duration_ms": 0,
            "error_type": result.error[:100] if result.error else None,
            "error_message": result.stderr[:500] if result.stderr else (result.error[:500] if result.error else None),
        })
    return {
        "status": "success" if all(r["exit_code"] == 0 for r in results) else "degraded",
        "connector": "github_cli",
        "commands": results,
        "request_id": request_id,
        "github_client_id_configured": bool(GITHUB_CLIENT_ID),
    }
