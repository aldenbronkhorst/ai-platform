"""Canonical tool records seeded into the database."""

from __future__ import annotations

from typing import Any


CANONICAL_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "workspace",
        "display_name": "Workspace",
        "description": (
            "Cloud workspace with Python and shell/terminal execution, file scratch work, and multi-step analysis. "
            "Use this for multi-step work: 3+ connector/tool calls, loops, pagination, batch updates, retries, "
            "conditional branching, large-output filtering, file transforms, aggregation, calculations, or temporary files. "
            "Prefer one workspace script that performs the full loop and prints/saves the result over many model-managed "
            "tool turns. Save files the user should receive under outputs/; only files in outputs/ "
            "are returned as chat attachments. Workspace Python has call(tool_name, arguments), call_raw(tool_name, arguments), list_files(), file_info(ref), "
            "download_file(ref), read_document(ref), read_tables(ref), read_layout(ref), save_output(filename, data), "
            "and output_path(filename) available by default. "
            "call() returns the connector result and raises on connector failure; use call_raw() only when the raw broker envelope is required. "
            "It can call connected-system broker targets through the connected user's credentials without exposing "
            "connector secrets. Uploaded/session files are visible through list_files(); use the document helpers for "
            "OCR text, tables, layout, and raw downloads before using ad hoc PDF libraries. Do not save deliverables to Desktop/Downloads "
            "or open local files; save them under outputs/ so the platform returns them to the user."
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
            "Built-in platform tool for uploaded PDFs/images. Reads native text, OCR text, structured tables, page layout, "
            "and raw uploaded bytes for Workspace transforms. Use this before workspace/PyMuPDF for uploaded document "
            "questions and comparisons. The tool owns its SKILL.md guidance; use mode='guidance' to inspect it. "
            "Use mode='tables' for invoices, GRVs, statements, price lists, purchase orders, bills, sales orders, "
            "credit notes, or any tabular comparison. Use mode='download' from Workspace code when a script must "
            "transform the original uploaded file, then save the result under outputs/."
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
