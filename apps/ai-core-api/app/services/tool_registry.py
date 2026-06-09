"""Canonical model-facing tool registry for connected accounts."""

from __future__ import annotations

from typing import Mapping

CONNECTOR_TOOLS_BY_SYSTEM: Mapping[str, frozenset[str]] = {
    "odoo": frozenset({"odoo_ops_runner"}),
    "microsoft_admin": frozenset(
        {
            "ms_graph",
            "ms_graph_powershell",
            "ms_exchange_powershell",
            "ms_teams_powershell",
            "ms_sharepoint_pnp_powershell",
            "ms_az_powershell",
            "ms_azure_cli",
            "ms_bicep",
        }
    ),
    "github": frozenset({"github_cli"}),
}

CONNECTOR_SYSTEMS = frozenset(CONNECTOR_TOOLS_BY_SYSTEM)
CONSOLIDATED_TOOL_NAMES = frozenset(
    tool_name
    for tool_names in CONNECTOR_TOOLS_BY_SYSTEM.values()
    for tool_name in tool_names
)
MICROSOFT_ADMIN_TOOL_NAMES = CONNECTOR_TOOLS_BY_SYSTEM["microsoft_admin"]


def is_model_facing_tool(name: str, target_system: str) -> bool:
    """Return whether an AITool row should be exposed by default.

    Connector tools are intentionally broad model-facing surfaces. Odoo uses one
    API/RPC runner; Microsoft exposes its native admin interfaces separately.
    Other connector-scoped AITool rows are internal/configuration debt and
    should not be shown to the model, context endpoint, or default tools list.
    """
    if target_system not in CONNECTOR_SYSTEMS:
        return True
    return name in CONNECTOR_TOOLS_BY_SYSTEM[target_system]
