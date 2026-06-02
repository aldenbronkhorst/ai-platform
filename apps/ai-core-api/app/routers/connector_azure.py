"""Azure CLI connector — hybrid az CLI + Azure SDK diagnostics."""
import logging
import uuid
from typing import Any
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from app.core.security import api_key_auth
from app.services.ops_command_runner import run_command

router = APIRouter(prefix="/connector/azure", tags=["Connector"])
logger = logging.getLogger(__name__)


class AzureCliRequest(BaseModel):
    command: str = Field(..., description="Azure CLI command")
    purpose: str = Field("", description="Why this command is needed")
    timeout: int = Field(60, description="Command timeout", le=300)


@router.post("/cli")
async def azure_cli(req: AzureCliRequest, auth: dict = Depends(api_key_auth)):
    command = req.command.strip()
    if not command.startswith("az "):
        command = "az " + command
    request_id = uuid.uuid4().hex[:16]
    result = await run_command(command, timeout=req.timeout)
    output = result.to_dict()
    output.update({"command": command, "connector": "azure_cli", "request_id": request_id,
                    "status": "success" if result.success else "failed"})
    return output


@router.post("/diagnose")
async def azure_diagnose(auth: dict = Depends(api_key_auth)):
    """Run Azure diagnostics using az CLI + Azure SDK for account info."""
    request_id = uuid.uuid4().hex[:16]
    commands = []
    all_ok = True

    # 1. Check az CLI availability
    r = await run_command("az --version", timeout=15)
    ok = r.exit_code == 0 and not r.error
    commands.append({"command": "az --version", "exit_code": r.exit_code, "stdout": r.stdout[:2000], "stderr": r.stderr[:1000],
                      "error_type": r.error[:100] if r.error else None,
                      "error_message": r.stderr[:300] if r.stderr else (r.error[:300] if r.error else None),
                      "status": "success" if ok else "failed"})
    all_ok = all_ok and ok

    # 2. Try az login with Managed Identity via env var
    r = await run_command("az login --identity --allow-no-subscriptions -o json", timeout=30)
    ok = r.exit_code == 0 and not r.error
    commands.append({"command": "az login --identity", "exit_code": r.exit_code,
                      "stdout": r.stdout[:2000], "stderr": r.stderr[:1000],
                      "error_type": r.error[:100] if r.error else None,
                      "error_message": r.stderr[:300] if r.stderr else (r.error[:300] if r.error else None),
                      "status": "success" if ok else "failed"})
    all_ok = all_ok and ok

    # 3. If az login succeeded, show account
    if ok:
        r = await run_command("az account show -o json", timeout=15)
        ok2 = r.exit_code == 0
        commands.append({"command": "az account show", "exit_code": r.exit_code, "stdout": r.stdout[:2000], "stderr": r.stderr[:1000],
                          "status": "success" if ok2 else "failed"})
        all_ok = all_ok and ok2

    # 4. SDK-based diagnostic (works without az login)
    try:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        token = cred.get_token("https://management.azure.com/.default")
        commands.append({"command": "SDK DefaultAzureCredential (Management API)", "exit_code": 0,
                          "stdout": f"Token acquired: type={type(token).__name__}", "stderr": "",
                          "status": "success"})
    except Exception as e:
        commands.append({"command": "SDK DefaultAzureCredential", "exit_code": 1, "stdout": "", "stderr": str(e)[:500],
                          "error_type": "sdk_auth_error", "error_message": str(e)[:300],
                          "status": "failed"})
        all_ok = False

    # 5. Key Vault access check
    import os
    kv_uri = os.environ.get("KEY_VAULT_URI", "")
    if kv_uri:
        try:
            from azure.keyvault.secrets import SecretClient
            cred = DefaultAzureCredential()
            client = SecretClient(vault_url=kv_uri, credential=cred)
            props = list(client.list_properties_of_secrets(max_page_size=3))
            commands.append({"command": f"Key Vault list secrets", "exit_code": 0,
                              "stdout": f"Connected. Found {len(props)} secrets.", "stderr": "", "status": "success"})
        except Exception as e:
            commands.append({"command": "Key Vault list secrets", "exit_code": 1, "stdout": "", "stderr": str(e)[:500],
                              "error_type": "kv_error", "error_message": str(e)[:300], "status": "failed"})
            all_ok = False

    return {"status": "success" if all_ok else "failed",
            "summary": "Azure diagnostics passed" if all_ok else "Azure diagnostics: some checks failed",
            "connector": "azure_cli", "commands": commands, "request_id": request_id}
