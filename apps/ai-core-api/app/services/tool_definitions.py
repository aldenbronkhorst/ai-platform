"""Canonical tool records seeded into the database."""

from __future__ import annotations

from typing import Any


CANONICAL_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "odoo",
        "display_name": "Odoo",
        "description": "Direct Odoo RPC access using the connected account. Call any Odoo model method with model, method, args, and kwargs, or provide calls for ordered raw calls. The connector injects credentials and uses the connected Odoo user's permissions.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name."},
                "method": {"type": "string", "description": "Odoo model method."},
                "args": {"type": "array", "items": {}, "description": "Positional method arguments."},
                "kwargs": {"type": "object", "description": "Keyword method arguments."},
                "calls": {"type": "array", "items": {"type": "object"}, "description": "Optional ordered list of raw Odoo calls. Each call may include name, model, method, args, and kwargs."},
                "continue_on_error": {"type": "boolean", "description": "For calls: return per-call errors instead of aborting at the first failed call.", "default": False},
            },
            "required": [],
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
        "description": "Built-in platform tool for uploaded documents. Extracts text from text-based PDFs locally and uses Azure Document Intelligence for OCR when a PDF or image does not contain extractable text. Does not depend on Azure AI Search.",
        "target_system": "ai-platform",
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "Uploaded artifact ID to inspect"},
                "mode": {"type": "string", "enum": ["status", "preview", "extract"], "description": "Read-only document operation"},
                "max_chars": {"type": "integer", "description": "Maximum extracted text characters to return", "default": 12000},
            },
            "required": ["artifact_id", "mode"],
        },
    },
]
