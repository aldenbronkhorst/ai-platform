"""Azure CLI connector — executes native az commands with structured diagnostics."""
import logging
import uuid
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
    request_id = uuid.uuid4().hex[:16]
    result = await run_command(command, timeout=req.timeout)
    output = result.to_dict()
    output["command"] = command
    output["purpose"] = req.purpose
    output["connector"] = "azure_cli"
    output["request_id"] = request_id
    output["status"] = "success" if result.success else "failed"
    if not result.success:
        logger.warning("Azure CLI failed | exit=%d error=%s", result.exit_code, result.error or result.stderr[:200])
    return output


@router.post("/diagnose")
async def azure_diagnose(auth: dict = Depends(api_key_auth)):
    """Run Azure CLI diagnostics: version check, auth, and resource access."""
    request_id = uuid.uuid4().hex[:16]
    commands = [
        "az --version",
        "az account show -o json",
        "az account list --query '[].{name:name, id:id, tenantId:tenantId}' -o json",
    ]
    results = []
    all_ok = True
    for cmd in commands:
        result = await run_command(cmd, timeout=30)
        cmd_ok = result.exit_code == 0 and not result.error
        results.append({
            "command": cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout[:5000] if result.stdout else "",
            "stderr": result.stderr[:2000] if result.stderr else "",
            "duration_ms": 0,
            "error_type": result.error[:100] if result.error else None,
            "error_message": result.error[:500] if result.error else (result.stderr[:500] if result.stderr else None),
            "status": "success" if cmd_ok else "failed",
        })
        if not cmd_ok:
            all_ok = False

    return {
        "status": "success" if all_ok else "failed",
        "summary": "Azure CLI diagnostics passed" if all_ok else "Azure CLI diagnostics failed",
        "connector": "azure_cli",
        "commands": results,
        "request_id": request_id,
    }
