"""Connector-owned Odoo skill access."""

from __future__ import annotations

import re
import json
from functools import lru_cache
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PACKAGE_ROOT / "connector.json"


@lru_cache(maxsize=1)
def connector_manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _primary_skill_path() -> Path:
    skills = connector_manifest().get("skills")
    if not isinstance(skills, list) or not skills:
        raise RuntimeError("Odoo connector manifest does not declare a skill.")
    skill = skills[0]
    if not isinstance(skill, dict) or not isinstance(skill.get("path"), str):
        raise RuntimeError("Odoo connector manifest skill path is invalid.")
    return PACKAGE_ROOT / skill["path"]


@lru_cache(maxsize=1)
def skill_markdown() -> str:
    return _primary_skill_path().read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def guidance_version() -> str:
    match = re.search(r"(?m)^version:\s*([^\n]+)\s*$", skill_markdown())
    return match.group(1).strip().strip('"') if match else "unknown"


def guidance_payload() -> dict[str, object]:
    return {
        "connector": "odoo",
        "name": "Odoo API",
        "version": guidance_version(),
        "format": "markdown",
        "source": str(_primary_skill_path()),
        "manifest": connector_manifest(),
        "content": skill_markdown(),
        "operations": {
            "guidance": "Return this connector-owned SKILL.md.",
            "execute_kw": "Run raw Odoo model methods through JSON-RPC execute_kw.",
            "batch": "Run multiple raw execute_kw calls in one connector request.",
        },
    }
