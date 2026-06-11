"""ms_graph_powershell runner for the native Microsoft Graph connector."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.services.connectors.microsoft_admin.powershell_common import (
    _microsoft_admin_token_env,
    _prepare_microsoft_admin_powershell_script,
    _run_microsoft_admin_powershell_tool,
)
from app.services.connectors.microsoft_admin.tokens import get_microsoft_admin_token

async def run_ms_graph_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Microsoft Graph PowerShell through the Microsoft Graph connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_graph_powershell",
    )
    if error:
        return error
    token = await get_microsoft_admin_token(user_id, "graph")
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_GRAPH_ACCESS_TOKEN",
        username_token=token,
    )
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_graph_powershell",
        token_env=token_env,
        preamble=_ms_graph_powershell_preamble(),
        required_env=("AI_PLATFORM_GRAPH_ACCESS_TOKEN",),
    )

def _ms_graph_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformGraph {
    if (-not $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) { throw 'Microsoft Graph token is not available. Reconnect Microsoft Graph and check the signed-in user''s directory roles.' }
    Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    $secureToken = ConvertTo-SecureString $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN -AsPlainText -Force
    Connect-MgGraph -AccessToken $secureToken -NoWelcome
}
"""
