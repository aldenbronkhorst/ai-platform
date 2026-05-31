import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from app.core.config import get_settings
from app.models.models import AITool

settings = get_settings()

# Tool names use underscores (OpenAI function-calling requires `^[a-zA-Z0-9_]+$`)
# input_schema must be valid JSON Schema for the OpenAI tools API.
TOOLS = [
    {
        "name": "odoo_execute_report",
        "display_name": "Odoo Accounting Report",
        "description": "Execute any accounting/financial report from Odoo (e.g. Profit and Loss, Balance Sheet, Trial Balance, Aged Receivables/Payables, Partner Ledger, General Ledger, Tax Report). Normalizes and flattens report lines.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "report_name": {"type": "string", "description": "The name or alias of the report to run (e.g. 'Profit and Loss', 'Balance Sheet', 'Trial Balance')"},
                "report_id": {"type": "integer", "description": "Specific Odoo report ID (optional)"},
                "date_from": {"type": "string", "description": "Start date for the report (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date for the report (YYYY-MM-DD)"},
                "company_id": {"type": "integer", "description": "Specific Odoo company ID"},
                "line_names": {"type": "array", "items": {"type": "string"}, "description": "Specific line names to filter in the report"},
                "include_raw_lines": {"type": "boolean", "description": "Include raw unflattened Odoo lines (default false)"},
            },
            "required": ["report_name"],
        },
    },
    {
        "name": "odoo_get_profit_and_loss",
        "display_name": "Odoo Profit and Loss Report",
        "description": "Retrieve the Profit and Loss (P&L) statement/report from Odoo for a given date range. Bypassed in favor of odoo_execute_report but maintained as compatibility alias.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date for the report (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date for the report (YYYY-MM-DD)"},
                "company_id": {"type": "integer", "description": "Specific Odoo company ID"},
                "currency": {"type": "string", "description": "Currency code (e.g. ZAR, USD)"},
            },
        },
    },
    {
        "name": "odoo_search_read",
        "display_name": "Odoo Search Read",
        "description": "Search and read records from any Odoo model. Supports domain filtering, field selection, pagination, and ordering.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name (e.g. res.partner, sale.order, product.product)"},
                "domain": {"type": "array", "items": {}, "description": "Search domain as list of tuples, e.g. [['is_company', '=', True]]"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to return (omit for id+name_get)"},
                "limit": {"type": "integer", "description": "Maximum records to return (default 50)", "default": 50},
                "offset": {"type": "integer", "description": "Number of records to skip", "default": 0},
                "order": {"type": "string", "description": "Sort order, e.g. 'id desc'"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_execute_kw",
        "display_name": "Odoo Execute Method",
        "description": "Execute a method on an Odoo model. Use for search, read, name_get, fields_get, browse and other read methods. Write methods require explicit approval.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name"},
                "method": {"type": "string", "description": "Method name (e.g. search, read, name_get, fields_get)"},
                "args": {"type": "array", "items": {}, "description": "Positional arguments for the method"},
                "kwargs": {"type": "object", "description": "Keyword arguments for the method"},
            },
            "required": ["model", "method"],
        },
    },
    {
        "name": "odoo_schema",
        "display_name": "Odoo Schema",
        "description": "Get schema information for Odoo models. Returns model list or field definitions for a specific model.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name to get fields for (omit to list all models)"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Specific fields to describe"},
            },
        },
    },
    {
        "name": "odoo_attachments_list",
        "display_name": "Odoo List Attachments",
        "description": "List attachments (ir.attachment) for a given Odoo record.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name the record belongs to"},
                "record_id": {"type": "integer", "description": "ID of the record"},
                "limit": {"type": "integer", "description": "Maximum attachments to return", "default": 20},
            },
            "required": ["model", "record_id"],
        },
    },
    {
        "name": "odoo_attachments_get",
        "display_name": "Odoo Get Attachment",
        "description": "Get metadata for a specific attachment by ID.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "integer", "description": "ID of the attachment (ir.attachment)"},
            },
            "required": ["attachment_id"],
        },
    },
    {
        "name": "odoo_messages_list",
        "display_name": "Odoo List Messages",
        "description": "List chatter messages (mail.message) for a given Odoo record.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name the record belongs to"},
                "record_id": {"type": "integer", "description": "ID of the record"},
                "limit": {"type": "integer", "description": "Maximum messages to return", "default": 20},
            },
            "required": ["model", "record_id"],
        },
    },
    {
        "name": "odoo_messages_create",
        "display_name": "Odoo Create Message",
        "description": "Post a chatter message on an Odoo record.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model name"},
                "record_id": {"type": "integer", "description": "ID of the record"},
                "body": {"type": "string", "description": "Message body text"},
            },
            "required": ["model", "record_id", "body"],
        },
    },
    {
        "name": "github_search_repo",
        "display_name": "GitHub Search Repo",
        "description": "Search within a GitHub repository",
        "target_system": "github",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name (owner/repo)"},
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["repo", "query"],
        },
    },
    {
        "name": "github_create_pr",
        "display_name": "GitHub Create PR",
        "description": "Create a pull request on GitHub",
        "target_system": "github",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name (owner/repo)"},
                "title": {"type": "string", "description": "PR title"},
                "body": {"type": "string", "description": "PR description"},
                "head": {"type": "string", "description": "Source branch"},
                "base": {"type": "string", "description": "Target branch"},
            },
            "required": ["repo", "title", "head", "base"],
        },
    },
    {
        "name": "runner_run_python",
        "display_name": "Runner Run Python",
        "description": "Run a Python script in a secure sandboxed runner",
        "target_system": "runner",
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Python script to execute"},
                "inputs": {"type": "object", "description": "Input variables for the script"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "ai_save_artifact",
        "display_name": "AI Save Artifact",
        "description": "Save an artifact to AI Platform blob storage",
        "target_system": "ai-platform",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Artifact filename"},
                "content_type": {"type": "string", "description": "MIME type"},
            },
            "required": ["filename"],
        },
    },
]


async def seed_tools():
    engine = create_async_engine(settings.database_url, future=True)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        for tool_data in TOOLS:
            result = await session.execute(select(AITool).where(AITool.name == tool_data["name"]))
            existing = result.scalar_one_or_none()
            if not existing:
                tool = AITool(**tool_data)
                session.add(tool)
                print(f"Added tool: {tool_data['name']}")
            else:
                print(f"Tool already exists: {tool_data['name']}")
        await session.commit()
    await engine.dispose()
    print("Tool seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed_tools())
