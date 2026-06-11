"""ms_az_powershell runner for the native Azure CLI connector."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.services.connectors.microsoft_admin.powershell_common import (
    _microsoft_admin_token_env,
    _prepare_microsoft_admin_powershell_script,
    _run_microsoft_admin_powershell_tool,
)
from app.services.connectors.microsoft_admin.tokens import get_microsoft_admin_token

async def run_ms_az_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Az PowerShell through the Azure CLI connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_az_powershell",
    )
    if error:
        return error
    token = await get_microsoft_admin_token(user_id, "arm")
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_ARM_ACCESS_TOKEN",
        username_token=token,
    )
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_az_powershell",
        token_env=token_env,
        preamble=_ms_az_powershell_preamble(),
        required_env=("AI_PLATFORM_ARM_ACCESS_TOKEN", "AI_PLATFORM_MS_USERNAME"),
    )

def _ms_az_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformAz {
    if (-not $env:AI_PLATFORM_ARM_ACCESS_TOKEN) { throw 'Azure Resource Manager token is not available. Reconnect Azure CLI and check the signed-in user''s Azure RBAC access.' }
    Import-Module Az.Accounts -ErrorAction Stop
    $secureToken = ConvertTo-SecureString $env:AI_PLATFORM_ARM_ACCESS_TOKEN -AsPlainText -Force
    Connect-AzAccount -AccessToken $secureToken -AccountId $env:AI_PLATFORM_MS_USERNAME -Tenant $env:AZURE_TENANT_ID | Out-Null
}
"""
