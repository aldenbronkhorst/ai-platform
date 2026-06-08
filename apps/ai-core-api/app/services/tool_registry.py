"""Canonical model-facing tool registry for connected accounts."""

from __future__ import annotations

from typing import Mapping

CONNECTOR_TOOL_BY_SYSTEM: Mapping[str, str] = {
    "odoo": "odoo_ops_runner",
    "azure": "ms_admin",
    "github": "github_cli",
}

CONNECTOR_SYSTEMS = frozenset(CONNECTOR_TOOL_BY_SYSTEM)
CONSOLIDATED_TOOL_NAMES = frozenset(CONNECTOR_TOOL_BY_SYSTEM.values())


def is_model_facing_tool(name: str, target_system: str) -> bool:
    """Return whether an AITool row should be exposed by default.

    Connector tools are intentionally one broad tool per connected system. Any
    other connector-scoped AITool row is internal/configuration debt and should
    not be shown to the model, context endpoint, or default tools listing.
    Non-connector tools keep their normal visibility.
    """
    if target_system not in CONNECTOR_SYSTEMS:
        return True
    return CONNECTOR_TOOL_BY_SYSTEM.get(target_system) == name
