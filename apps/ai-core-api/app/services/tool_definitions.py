"""Canonical tool records seeded into the database."""

from __future__ import annotations

from typing import Any


CANONICAL_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "workspace",
        "display_name": "Workspace",
        "description": (
            "Cloud workspace with Python and shell/terminal execution, files shared across tool calls in a turn, uploaded-file access, "
            "and brokered access to the user's connected systems. Python includes helpers for calling tools, inspecting "
            "files, reading documents, and creating output artifacts. Files saved under outputs/ are returned to the user "
            "and remain available in the chat."
        ),
        "target_system": "ai-platform",
        "input_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "shell", "bash", "sh", "terminal"], "description": "Execution language or terminal mode.", "default": "python"},
                "code": {"type": "string", "description": "Python code or shell commands to run in the workspace."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 600).", "default": 60},
                "purpose": {"type": "string", "description": "Short reason why a workspace script is needed."},
                "files": {
                    "type": "array",
                    "description": "Optional text files to create before execution.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative path inside the workspace."},
                            "content": {"type": "string", "description": "UTF-8 text content."},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "document_reader",
        "display_name": "Document Reader",
        "description": (
            "Reads uploaded PDFs and images as native text, OCR text, structured tables, page layout, or raw bytes. "
            "Its SKILL.md is available with mode='guidance'."
        ),
        "target_system": "ai-platform",
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "Uploaded artifact ID to inspect. Not required for mode='guidance'."},
                "mode": {"type": "string", "enum": ["guidance", "status", "read", "preview", "extract", "tables", "layout", "download"], "description": "Document operation. guidance returns the tool-owned SKILL.md; tables returns structured rows/cells using layout OCR; layout returns page lines/geometry; read returns line-numbered text; download returns base64 original file bytes for Workspace transforms."},
                "offset": {"type": "integer", "description": "Line number to start reading from in mode='read' (1-indexed).", "default": 1, "minimum": 1},
                "limit": {"type": "integer", "description": "Maximum lines to read in mode='read' (default 500, max 2000).", "default": 500, "maximum": 2000},
                "table_offset": {"type": "integer", "description": "Table number to start reading from in mode='tables' (1-indexed).", "default": 1, "minimum": 1},
                "table_limit": {"type": "integer", "description": "Maximum structured tables to return in mode='tables' (default 20, max 100).", "default": 20, "maximum": 100},
                "page_offset": {"type": "integer", "description": "Page number to start reading from in mode='layout' (1-indexed).", "default": 1, "minimum": 1},
                "page_limit": {"type": "integer", "description": "Maximum layout pages to return in mode='layout' (default 20, max 100).", "default": 20, "maximum": 100},
                "max_chars": {"type": "integer", "description": "Maximum extracted text characters to return", "default": 12000},
            },
            "required": ["mode"],
        },
    },
]
