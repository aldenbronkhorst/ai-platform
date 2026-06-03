import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from app.core.config import get_settings
from app.models.models import AITool

settings = get_settings()

TOOLS = [
    # ── Primary Connector Surface ──

    {
        "name": "odoo_ops_runner",
        "display_name": "Odoo Operations Runner",
        "description": "Consolidated Odoo command center. Modes: health, schema, query/records, count, aggregate, report/account_report, attachment, content, message, mutation/create/write/delete, execute. Use the mode field to choose the operation. Set `mode` to one of the supported values and provide the required parameters for that mode.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["health", "schema", "query", "records", "count", "aggregate", "report", "account_report", "attachment", "content", "message", "mutation", "create", "write", "delete", "execute"], "description": "Operation mode"},
                "model": {"type": "string", "description": "Odoo model name (required for most modes)"},
                "domain": {"type": "array", "items": {}, "description": "Search domain as list of filters"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to return"},
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific record IDs"},
                "limit": {"type": "integer", "description": "Max records (default 50)", "default": 50},
                "offset": {"type": "integer", "description": "Records to skip", "default": 0},
                "order": {"type": "string", "description": "Sort order, e.g. 'id desc'"},
                "report_name": {"type": "string", "description": "Report name or alias (e.g. 'Profit and Loss', 'Trial Balance')"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "line_names": {"type": "array", "items": {"type": "string"}, "description": "Optional exact report line names to filter when already known"},
                "attachment_id": {"type": "integer", "description": "Single attachment ID"},
                "content_fields": {"type": "array", "items": {"type": "string"}, "description": "Content fields to read"},
                "operation": {"type": "string", "enum": ["create", "write", "delete"], "description": "Mutation operation"},
                "values": {"type": "object", "description": "Field values for create/write"},
                "body": {"type": "string", "description": "Message body text"},
                "purpose": {"type": "string", "description": "Short reason why content is needed"},
                "query": {"type": "string", "description": "Search query for schema mode"},
                "method": {"type": "string", "description": "Execute method (execute mode)"},
                "args": {"type": "array", "items": {}, "description": "Arguments for execute mode"},
                "kwargs": {"type": "object", "description": "Keyword arguments for execute mode"},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "azure_cli",
        "display_name": "Azure CLI",
        "description": "Execute native Azure CLI commands. Use for any Azure operation: container apps, revisions, logs, metrics, key vault, network, etc. Uses the connected user's Azure account. Provide the full az command without the 'az ' prefix.",
        "target_system": "azure",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Azure CLI command (e.g. 'containerapp revision list --name ca-ai-platform-api-prod-san-001 --resource-group rg-ai-platform-prod-san-001')"},
                "purpose": {"type": "string", "description": "Short reason why this command is needed"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 300)", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "github_cli",
        "display_name": "GitHub CLI",
        "description": "Execute native GitHub CLI commands (gh, git, rg, jq). Use for any GitHub operation: repo view, run list, PR view, issue list, commit log, file search, etc. Uses stored GitHub token for authentication.",
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

        await session.commit()
        print("Tools seeded successfully.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_tools())
