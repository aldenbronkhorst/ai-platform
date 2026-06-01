"""Azure CLI connector — executes native az commands."""
import logging
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends
from app.core.security import api_key_auth
from app.services.ops_command_runner import run_command

router = APIRouter(prefix="/connector/azure", tags=["Connector"])
logger = logging.getLogger(__name__)


class AzureCliRequest(BaseModel):
    command: str = Field(..., description="Azure CLI command (without 'az' prefix)")
    purpose: str = Field("", description="Why this command is needed")
    timeout: int = Field(60, description="Command timeout in seconds", le=300)


@router.post("/cli")
async def azure_cli(req: AzureCliRequest, auth: dict = Depends(api_key_auth)):
    """Execute an Azure CLI command using Managed Identity authentication."""
    command = req.command.strip()
    if not command.startswith("az "):
        command = "az " + command

    logger.info("Azure CLI | command=%.100s purpose=%.100s", command, req.purpose)
    result = await run_command(command, timeout=req.timeout)
    output = result.to_dict()
    output["command"] = command
    output["purpose"] = req.purpose

    if not result.success:
        logger.warning("Azure CLI failed | exit=%d error=%s", result.exit_code, result.error or result.stderr[:200])

    return output
