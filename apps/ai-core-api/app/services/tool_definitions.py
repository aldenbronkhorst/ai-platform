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
        "name": "ms_azure_cli",
        "display_name": "Azure Resource Manager CLI",
        "description": "Native Azure CLI connector. Runs user-scoped az commands from the connected Azure CLI session. Use for Azure subscriptions, resources, resource groups, Container Apps, Key Vault, storage, RBAC, logs, and Azure Cost Management via az rest. Do not use az costmanagement query; use az rest against the Microsoft.CostManagement query endpoint and answer cost questions only from successful tool results. GitHub commands are excluded.",
        "target_system": "azure_cli",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Azure CLI command. May include or omit the leading az."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
                "purpose": {"type": "string", "description": "Short reason why this Azure CLI command is needed"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "ms_graph",
        "display_name": "Microsoft Graph",
        "description": "Direct Microsoft Graph connector. Use for Entra/Microsoft 365 users, groups, licensing, Intune, managed devices, directory objects, and Graph APIs. Uses the signed-in user's Microsoft Graph token and consent. Path must start with /. GET collection requests auto-follow @odata.nextLink where practical; do not invent manual $skip paging for /users. Microsoft Graph permissions and tenant consent decide access.",
        "target_system": "microsoft_graph",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT", "DELETE"], "description": "Microsoft Graph HTTP method", "default": "GET"},
                "path": {"type": "string", "description": "Microsoft Graph path starting with '/', e.g. /users?$top=5"},
                "api_version": {"type": "string", "enum": ["v1.0", "beta"], "description": "Microsoft Graph API version", "default": "v1.0"},
                "body": {"type": "object", "description": "JSON body for POST/PATCH/PUT"},
                "headers": {"type": "object", "description": "Optional additional Graph request headers"},
                "max_pages": {"type": "integer", "description": "Maximum Graph collection pages to auto-follow for GET requests (default 20)", "default": 20},
                "max_items": {"type": "integer", "description": "Maximum Graph collection items to return after auto-paging (default 1000)", "default": 1000},
                "purpose": {"type": "string", "description": "Short reason why this Graph request is needed"},
            },
            "required": ["method", "path"],
        },
    },
    {
        "name": "ms_exchange_powershell",
        "display_name": "Exchange Online PowerShell",
        "description": "Exchange Online PowerShell connector. Runs pwsh with the signed-in user's Exchange token. Use ExchangeOnlineManagement cmdlets for mailboxes, permissions, mail flow, transport rules, and message trace. Call Connect-AIPlatformExchange before authenticated Exchange cmdlets. GitHub commands are excluded.",
        "target_system": "exchange_online",
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "PowerShell script to run with pwsh. Call Connect-AIPlatformExchange before authenticated Exchange cmdlets."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
                "purpose": {"type": "string", "description": "Short reason why this Exchange PowerShell script is needed"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "ms_teams_powershell",
        "display_name": "Microsoft Teams PowerShell",
        "description": "Microsoft Teams PowerShell connector. Runs pwsh with the signed-in user's Teams admin token. Use MicrosoftTeams cmdlets for Teams admin work and policies. Call Connect-AIPlatformTeams before authenticated Teams cmdlets. GitHub commands are excluded.",
        "target_system": "teams_admin",
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "PowerShell script to run with pwsh. Call Connect-AIPlatformTeams before authenticated Teams cmdlets."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
                "purpose": {"type": "string", "description": "Short reason why this Teams PowerShell script is needed"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "ms_sharepoint_pnp_powershell",
        "display_name": "SharePoint PnP PowerShell",
        "description": "SharePoint/PnP PowerShell connector. Runs pwsh in the signed-in SharePoint/PnP shell for SharePoint and PnP.PowerShell work. Use for SharePoint admin/site automation where the user has permission and the required token/consent is available. GitHub commands are excluded.",
        "target_system": "sharepoint_pnp",
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "PowerShell script to run with pwsh for SharePoint/PnP work."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
                "purpose": {"type": "string", "description": "Short reason why this SharePoint/PnP script is needed"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "github_cli",
        "display_name": "GitHub CLI",
        "description": "Execute native GitHub CLI and local repo commands (gh, git, rg, jq). Use for GitHub operations such as repos, Actions, PRs, issues, commits, and code search. Uses the connected user's GitHub token; GitHub org/repo/app permissions decide access.",
        "target_system": "github",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "GitHub CLI command (e.g. 'gh run list --repo aldenbronkhorst/ai-platform --limit 10')"},
                "purpose": {"type": "string", "description": "Short reason why this command is needed"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "document_reader",
        "display_name": "Document Reader",
        "description": "Built-in platform tool for uploaded PDFs/images. Reads native text, OCR text, structured tables, page layout, and raw uploaded bytes for Workspace transforms. Use this before workspace/PyMuPDF for uploaded document questions and comparisons. The tool owns its SKILL.md guidance; use mode='guidance' to inspect it. Use mode='tables' for invoices, GRVs, statements, price lists, purchase orders, bills, sales orders, credit notes, or any tabular comparison. Use mode='download' from Workspace code when a script must transform the original uploaded file, then save the result under outputs/.",
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
