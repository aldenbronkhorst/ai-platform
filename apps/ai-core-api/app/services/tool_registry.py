"""Canonical tool registry for connected accounts."""

from __future__ import annotations

from typing import Mapping

from app.services.tool_definitions import CANONICAL_TOOL_DEFINITIONS

CANONICAL_TOOL_NAMES = frozenset(str(tool["name"]) for tool in CANONICAL_TOOL_DEFINITIONS)
CONNECTOR_SYSTEMS = frozenset({"odoo"})

CONNECTOR_TOOLS_BY_SYSTEM: Mapping[str, frozenset[str]] = {
    "odoo": frozenset(),
}

CONSOLIDATED_TOOL_NAMES = frozenset()


def is_model_facing_tool(name: str, target_system: str) -> bool:
    """Return whether an AITool row should be exposed by default."""
    if name not in CANONICAL_TOOL_NAMES:
        return False
    return target_system not in CONNECTOR_SYSTEMS
