"""Canonical tool registry for connected accounts."""

from __future__ import annotations

from typing import Mapping

from app.services.tool_definitions import CANONICAL_TOOL_DEFINITIONS

CANONICAL_TOOL_NAMES = frozenset(str(tool["name"]) for tool in CANONICAL_TOOL_DEFINITIONS)
MICROSOFT_NATIVE_CONNECTOR_SYSTEMS = (
    "azure_cli",
    "microsoft_graph",
    "exchange_online",
    "teams_admin",
    "sharepoint_pnp",
)

_CONNECTOR_SYSTEMS = ("odoo", *MICROSOFT_NATIVE_CONNECTOR_SYSTEMS, "github")

CONNECTOR_TOOLS_BY_SYSTEM: Mapping[str, frozenset[str]] = {
    system: frozenset(
        str(tool["name"])
        for tool in CANONICAL_TOOL_DEFINITIONS
        if tool["target_system"] == system
    )
    for system in _CONNECTOR_SYSTEMS
}

CONNECTOR_SYSTEMS = frozenset(CONNECTOR_TOOLS_BY_SYSTEM)
CONSOLIDATED_TOOL_NAMES = frozenset(
    tool_name
    for tool_names in CONNECTOR_TOOLS_BY_SYSTEM.values()
    for tool_name in tool_names
)
MICROSOFT_NATIVE_TOOL_NAMES = frozenset(
    tool_name
    for system in MICROSOFT_NATIVE_CONNECTOR_SYSTEMS
    for tool_name in CONNECTOR_TOOLS_BY_SYSTEM.get(system, frozenset())
)


def is_model_facing_tool(name: str, target_system: str) -> bool:
    """Return whether an AITool row should be exposed by default.

    Connectors are broker targets, not chat tools. The model gets the platform
    workspace and other built-in tools; workspace code can call every connected
    connector through the broker using the signed-in user's credentials.
    """
    if name not in CANONICAL_TOOL_NAMES:
        return False
    if target_system not in CONNECTOR_SYSTEMS:
        return True
    return False
