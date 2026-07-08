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


def _document_roots() -> list[Path]:
    """Directories whose markdown is fetchable on demand via the 'playbook' operation."""
    skill_dir = _primary_skill_path().parent
    return [skill_dir / "troubleshooting", skill_dir / "references"]


@lru_cache(maxsize=1)
def _document_index() -> dict[str, Path]:
    """Map a short document name (file stem) -> its path.

    Built once by scanning the troubleshooting/ and references/ trees. Callers look
    documents up only through this prebuilt index, never by constructing a path from a
    caller-supplied name, so path traversal is impossible: an unknown name is simply
    not a key.
    """
    index: dict[str, Path] = {}
    for base in _document_roots():
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            index.setdefault(path.stem, path)
    return index


def available_documents() -> list[str]:
    """Names of the on-demand troubleshooting documents (loop, router, playbooks, references)."""
    return sorted(_document_index().keys())


def document_markdown(name: str) -> str | None:
    """Return one troubleshooting document's markdown, or None if the name is unknown."""
    path = _document_index().get((name or "").strip())
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def guidance_payload() -> dict[str, object]:
    return {
        "connector": "odoo",
        "name": "Odoo API",
        "version": guidance_version(),
        "format": "markdown",
        "source": str(_primary_skill_path()),
        "manifest": connector_manifest(),
        "content": skill_markdown(),
        "documents": available_documents(),
        "operations": {
            "guidance": "Return this connector-owned SKILL.md.",
            "playbook": "Return one on-demand troubleshooting document by name (see 'documents').",
            "execute_kw": "Run raw Odoo model methods through JSON-RPC execute_kw.",
            "batch": "Run multiple raw execute_kw calls in one connector request.",
        },
    }
