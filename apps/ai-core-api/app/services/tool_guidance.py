"""Tool-owned guidance loading."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"


def _skill_path(tool_name: str) -> Path:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "", tool_name.strip())
    return TOOLS_ROOT / normalized / "SKILL.md"


@lru_cache(maxsize=32)
def tool_skill_markdown(tool_name: str) -> str | None:
    path = _skill_path(tool_name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def tool_guidance_payload(tool_name: str) -> dict[str, Any] | None:
    content = tool_skill_markdown(tool_name)
    if not content:
        return None
    version_match = re.search(r"(?m)^version:\s*([^\n]+)\s*$", content)
    version = version_match.group(1).strip().strip('"') if version_match else "unknown"
    path = _skill_path(tool_name)
    return {
        "tool": tool_name,
        "version": version,
        "format": "markdown",
        "source": str(path),
        "content": content,
    }
