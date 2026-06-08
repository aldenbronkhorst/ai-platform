import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, update
from app.core.config import get_settings
from app.models.models import AITool
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, CONNECTOR_SYSTEMS

settings = get_settings()

TOOLS = [
    # ── Primary Connector Surface ──

    {
        "name": "odoo_ops_runner",
        "display_name": "Odoo Operations Runner",
        "description": "Consolidated Odoo command center. Use one broad mode instead of feature-specific tools. Modes: health, schema, query, aggregate, report, attachment, content, message, mutation, execute. Normal ORM work uses XML-RPC; complex accounting reports use the connector report path. Record-level results include verified record_url values when a link can be built; never infer Odoo hostnames. For side effects such as message_post or mail.activity completion, only report success when effect_verified is true; message_post is record chatter, not a private Discuss direct message.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["health", "schema", "query", "records", "count", "aggregate", "report", "account_report", "attachment", "content", "message", "mutation", "execute"], "description": "Broad operation mode"},
                "model": {"type": "string", "description": "Odoo model name (required for most modes)"},
                "domain": {"type": "array", "items": {}, "description": "Search domain as list of filters. For chatter, mail.activity uses res_model/res_id; mail.message uses model/res_id, not res_model."},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to return"},
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific record IDs. Required for execute record methods unless record_id or args[0] is provided."},
                "record_id": {"type": "integer", "description": "Single target record ID for message mode or an execute record method."},
                "limit": {"type": "integer", "description": "Max records for this page (default 50)", "default": 50},
                "offset": {"type": "integer", "description": "Records to skip", "default": 0},
                "order": {"type": "string", "description": "Sort order, e.g. 'id desc'"},
                "report_name": {"type": "string", "description": "Report name or alias (e.g. 'Profit and Loss', 'Trial Balance')"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "line_names": {"type": "array", "items": {"type": "string"}, "description": "Optional exact report line names to filter when already known"},
                "attachment_id": {"type": "integer", "description": "Single attachment ID"},
                "attachment_ids": {"type": "array", "items": {"type": "integer"}, "description": "Multiple attachment IDs"},
                "content_fields": {"type": "array", "items": {"type": "string"}, "description": "Content fields to read"},
                "operation": {"type": "string", "enum": ["create", "write", "delete", "post"], "description": "Mutation operation, or post for message mode. Message mode defaults to post when omitted."},
                "values": {"type": "object", "description": "Field values for create/write"},
                "body": {"type": "string", "description": "Message body text for message mode"},
                "message_type": {"type": "string", "description": "Odoo chatter message type, usually comment"},
                "subtype_xmlid": {"type": "string", "description": "Odoo chatter subtype XML ID"},
                "partner_ids": {"type": "array", "items": {"type": "integer"}, "description": "Odoo partner IDs to notify on a chatter message"},
                "attachment_ids_for_message": {"type": "array", "items": {"type": "integer"}, "description": "Attachment IDs to include in a chatter message"},
                "raw_html": {"type": "boolean", "description": "Send message body as trusted HTML when supported"},
                "purpose": {"type": "string", "description": "Short reason why content is needed"},
                "query": {"type": "string", "description": "Search query for schema mode"},
                "method": {"type": "string", "description": "Execute method (execute mode). Record methods such as action_feedback/message_post/unlink need ids, record_id, or args=[[id]]."},
                "args": {"type": "array", "items": {}, "description": "Arguments for execute mode. For record methods, first item must be the record ID list, e.g. [[2180]]."},
                "kwargs": {"type": "object", "description": "Keyword arguments for execute mode"},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "ms_admin",
        "display_name": "Microsoft Admin Connector",
        "description": "Consolidated Microsoft Admin workstation connector. Provides user-scoped Microsoft admin tooling through modes: status, azure_cli, powershell, bicep, graph_request. Includes PowerShell 7, Microsoft Graph PowerShell, Exchange Online PowerShell, Microsoft Teams PowerShell, Az PowerShell, Azure CLI, Bicep CLI, and direct Microsoft Graph calls. PowerShell mode exposes Connect-AIPlatformAz, Connect-AIPlatformGraph, and Connect-AIPlatformExchange helpers for the signed-in Microsoft session when consented tokens are available. It intentionally excludes GitHub tooling; use the GitHub connector for gh/git work. Microsoft permissions, RBAC, and Graph/Exchange/Teams consent decide access.",
        "target_system": "azure",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["status", "azure_cli", "powershell", "bicep", "graph_request"], "description": "Operation mode. Use status for diagnostics, azure_cli for az commands, powershell for Microsoft admin PowerShell, bicep for Bicep CLI, graph_request for direct Microsoft Graph HTTP."},
                "command": {"type": "string", "description": "Command for azure_cli or bicep mode. Azure CLI commands may include or omit the leading az."},
                "script": {"type": "string", "description": "PowerShell script or command for powershell mode."},
                "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT", "DELETE"], "description": "HTTP method for graph_request mode", "default": "GET"},
                "path": {"type": "string", "description": "Microsoft Graph path for graph_request mode, starting with '/', e.g. /users?$top=5"},
                "api_version": {"type": "string", "enum": ["v1.0", "beta"], "description": "Microsoft Graph API version", "default": "v1.0"},
                "body": {"type": "object", "description": "JSON body for graph_request POST/PATCH/PUT"},
                "headers": {"type": "object", "description": "Optional additional Graph request headers"},
                "purpose": {"type": "string", "description": "Short reason why this command is needed"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
            },
            "required": ["mode"],
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


async def seed_tools():
    database_url = settings.database_url
    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        for tool_data in TOOLS:
            result = await session.execute(
                select(AITool).where(AITool.name == tool_data["name"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.display_name = tool_data["display_name"]
                existing.description = tool_data["description"]
                existing.target_system = tool_data["target_system"]
                existing.input_schema = tool_data["input_schema"]
            else:
                tool = AITool(
                    name=tool_data["name"],
                    display_name=tool_data["display_name"],
                    description=tool_data["description"],
                    target_system=tool_data["target_system"],
                    input_schema=tool_data["input_schema"],
                )
                session.add(tool)

        archived = await session.execute(
            update(AITool)
            .where(
                AITool.status == "active",
                AITool.target_system.in_(CONNECTOR_SYSTEMS),
                ~AITool.name.in_(CONSOLIDATED_TOOL_NAMES),
            )
            .values(status="archived", updated_at=datetime.now(timezone.utc))
        )
        await session.commit()
        archived_count = archived.rowcount or 0
        print(f"Tools seeded successfully. Archived {archived_count} non-canonical connector tool(s).")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_tools())
