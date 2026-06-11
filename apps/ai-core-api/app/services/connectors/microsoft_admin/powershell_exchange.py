"""ms_exchange_powershell runner for the native Exchange Online connector."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.services.connectors.microsoft_admin.powershell_common import (
    _microsoft_admin_token_env,
    _prepare_microsoft_admin_powershell_script,
    _run_microsoft_admin_powershell_tool,
)
from app.services.connectors.microsoft_admin.tokens import get_microsoft_admin_token

async def run_ms_exchange_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Exchange Online PowerShell through the Exchange Online connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_exchange_powershell",
    )
    if error:
        return error
    token = await get_microsoft_admin_token(user_id, "exchange")
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_EXCHANGE_ACCESS_TOKEN",
        username_token=token,
    )
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_exchange_powershell",
        token_env=token_env,
        preamble=_ms_exchange_powershell_preamble(),
        required_env=("AI_PLATFORM_EXCHANGE_ACCESS_TOKEN", "AI_PLATFORM_MS_USERNAME"),
    )

def _ms_exchange_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformExchange {
    if (-not $env:AI_PLATFORM_EXCHANGE_ACCESS_TOKEN) { throw 'Exchange Online token is not available. Reconnect Exchange Online and check the signed-in user''s Exchange admin roles.' }
    Import-Module ExchangeOnlineManagement -ErrorAction Stop
    Connect-ExchangeOnline -AccessToken $env:AI_PLATFORM_EXCHANGE_ACCESS_TOKEN -UserPrincipalName $env:AI_PLATFORM_MS_USERNAME -ShowBanner:$false
}
"""
