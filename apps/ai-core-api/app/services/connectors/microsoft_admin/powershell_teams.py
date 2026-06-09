"""ms_teams_powershell runner for the Microsoft Admin connector."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.services.connectors.microsoft_admin.powershell_common import (
    _failed_microsoft_admin_result,
    _microsoft_admin_token_env,
    _prepare_microsoft_admin_powershell_script,
    _run_microsoft_admin_powershell_tool,
)
from app.services.connectors.microsoft_admin.tokens import get_microsoft_admin_token

async def run_ms_teams_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Microsoft Teams PowerShell through the Microsoft Admin connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_teams_powershell",
    )
    if error:
        return error
    graph_token = await get_microsoft_admin_token(user_id, "graph")
    teams_token = await get_microsoft_admin_token(user_id, "teams")
    token_env = _microsoft_admin_token_env(
        graph_token,
        access_token_env="AI_PLATFORM_GRAPH_ACCESS_TOKEN",
        username_token=graph_token,
    )
    if teams_token and teams_token.get("access_token") and not teams_token.get("refresh_error"):
        token_env["AI_PLATFORM_TEAMS_ACCESS_TOKEN"] = teams_token["access_token"]
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_teams_powershell",
        token_env=token_env,
        preamble=_ms_teams_powershell_preamble(),
        required_env=("AI_PLATFORM_GRAPH_ACCESS_TOKEN", "AI_PLATFORM_TEAMS_ACCESS_TOKEN"),
    )

def _ms_teams_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformTeams {
    if (-not $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) { throw 'Microsoft Graph token is not available. Check Microsoft Admin tenant consent and the signed-in user''s Teams/admin roles.' }
    if (-not $env:AI_PLATFORM_TEAMS_ACCESS_TOKEN) { throw 'Microsoft Teams admin token is not available. Check Microsoft Admin tenant consent and the signed-in user''s Teams/admin roles.' }
    Import-Module MicrosoftTeams -ErrorAction Stop
    Connect-MicrosoftTeams -AccessTokens @($env:AI_PLATFORM_GRAPH_ACCESS_TOKEN, $env:AI_PLATFORM_TEAMS_ACCESS_TOKEN) | Out-Null
}
"""
