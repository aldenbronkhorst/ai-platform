"""OpenAI-compatible tool definition helpers."""

from __future__ import annotations

import logging
import re

from app.models.models import AITool

logger = logging.getLogger(__name__)


def _normalize_tool_name(name: str) -> str:
    """Return a provider-safe tool name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


def _build_tool_definitions(tools: list[AITool]) -> list[dict]:
    """Convert active AITool rows to OpenAI-compatible function tools."""
    definitions = []
    for tool in tools:
        schema = tool.input_schema
        if not schema:
            continue
        normalized = _normalize_tool_name(tool.name)
        if normalized != tool.name:
            logger.info("Normalized tool name '%s' to '%s'", tool.name, normalized)
        definitions.append({
            "type": "function",
            "function": {
                "name": normalized,
                "description": tool.description or "",
                "parameters": schema,
            },
        })
    return definitions
