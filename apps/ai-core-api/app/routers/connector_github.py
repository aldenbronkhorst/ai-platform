"""GitHub CLI connector — executes native gh and git commands."""
import logging
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends
from app.core.security import api_key_auth
from app.services.ops_command_runner import run_command

router = APIRouter(prefix="/connector/github", tags=["Connector"])
logger = logging.getLogger(__name__)


class GithubCliRequest(BaseModel):
    command: str = Field(..., description="GitHub CLI command (gh, git, rg, jq)")
    purpose: str = Field("", description="Why this command is needed")
    timeout: int = Field(60, description="Command timeout in seconds", le=300)
    repo_dir: Optional[str] = Field(None, description="Working directory for git commands")


@router.post("/cli")
async def github_cli(req: GithubCliRequest, auth: dict = Depends(api_key_auth)):
    """Execute a GitHub CLI command using stored GitHub token."""
    command = req.command.strip()

    logger.info("GitHub CLI | command=%.100s purpose=%.100s", command, req.purpose)
    result = await run_command(command, timeout=req.timeout, cwd=req.repo_dir)
    output = result.to_dict()
    output["command"] = command
    output["purpose"] = req.purpose

    if not result.success:
        logger.warning("GitHub CLI failed | exit=%d error=%s", result.exit_code, result.error or result.stderr[:200])

    return output
