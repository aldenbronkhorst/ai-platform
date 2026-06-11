"""ms_sharepoint_pnp_powershell runner for the native SharePoint/PnP connector."""
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

async def run_ms_sharepoint_pnp_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute SharePoint/PnP PowerShell through the SharePoint/PnP connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_sharepoint_pnp_powershell",
    )
    if error:
        return error
    site_url = str(arguments.get("site_url") or arguments.get("admin_url") or "").strip()
    if not site_url:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="ms_sharepoint_pnp_powershell",
            message="Provide site_url or admin_url for ms_sharepoint_pnp_powershell.",
            connector="ms_sharepoint_pnp_powershell",
            error_type="missing_site_url",
        )
    token = await get_microsoft_admin_token(user_id, "sharepoint", site_url=site_url)
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_PNP_ACCESS_TOKEN",
        username_token=token,
    )
    token_env["AI_PLATFORM_PNP_URL"] = site_url
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_sharepoint_pnp_powershell",
        token_env=token_env,
        preamble=_ms_sharepoint_pnp_powershell_preamble(),
        required_env=("AI_PLATFORM_PNP_ACCESS_TOKEN", "AI_PLATFORM_PNP_URL"),
    )

def _ms_sharepoint_pnp_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformPnP {
    if (-not $env:AI_PLATFORM_PNP_ACCESS_TOKEN) { throw 'SharePoint/PnP token is not available. Reconnect SharePoint/PnP and check the signed-in user''s SharePoint permissions.' }
    if (-not $env:AI_PLATFORM_PNP_URL) { throw 'SharePoint site URL is required.' }
    Import-Module PnP.PowerShell -ErrorAction Stop
    Connect-PnPOnline -Url $env:AI_PLATFORM_PNP_URL -AccessToken $env:AI_PLATFORM_PNP_ACCESS_TOKEN
}
"""
