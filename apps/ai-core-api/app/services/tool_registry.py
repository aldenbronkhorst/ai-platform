"""Canonical model-facing tool registry."""

from __future__ import annotations

from app.services.tool_definitions import CANONICAL_TOOL_DEFINITIONS

CANONICAL_TOOL_NAMES = frozenset(str(tool["name"]) for tool in CANONICAL_TOOL_DEFINITIONS)


def is_model_facing_tool(name: str, target_system: str) -> bool:
    """Return whether an AITool row should be exposed by default."""
    return name in CANONICAL_TOOL_NAMES and target_system == "ai-platform"
