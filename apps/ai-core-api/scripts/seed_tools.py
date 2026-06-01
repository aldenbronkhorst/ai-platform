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
    # ── Primary Tool Surface ──

    {
        "name": "odoo_health",
        "display_name": "Odoo Health",
        "description": "Check Odoo connection health, authenticated user, database, and version. Use for debugging connection problems and confirming the connected account works.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "odoo_schema",
        "display_name": "Odoo Schema",
        "description": "Model and field discovery. Modes: search_models, inspect_model, fields, relations, hints. Use when unsure about correct Odoo model names, available fields, or field types.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search_models", "inspect_model", "fields", "relations", "hints"], "description": "Schema operation mode"},
                "model": {"type": "string", "description": "Odoo model name (required for inspect_model/fields/relations/hints)"},
                "query": {"type": "string", "description": "Search query for search_models mode"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Specific fields to inspect"},
                "limit": {"type": "integer", "description": "Max models to return (search_models mode)", "default": 50},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "odoo_query",
        "display_name": "Odoo Query",
        "description": "Fast generic read tool for any Odoo model. Modes: records, ids, count, summary. IDs included by default. Refuses large body/binary/content fields (use odoo_content instead). Default limit 50.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["records", "ids", "count", "summary"], "description": "Query mode", "default": "records"},
                "model": {"type": "string", "description": "Odoo model name (e.g. account.move, res.partner, product.product)"},
                "domain": {"type": "array", "items": {}, "description": "Search domain as list of tuples, e.g. [['is_company', '=', True]]"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to return. Do not request body/content/binary fields."},
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific record IDs to fetch"},
                "limit": {"type": "integer", "description": "Max records (default 50, max 200)", "default": 50},
                "offset": {"type": "integer", "description": "Records to skip", "default": 0},
                "order": {"type": "string", "description": "Sort order, e.g. 'id desc'"},
                "include_ids": {"type": "boolean", "description": "Include IDs in results (default true)", "default": True},
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_analyze",
        "display_name": "Odoo Analyze",
        "description": "Analysis, aggregation, pivot summaries, and accounting reports. Modes: aggregate, account_report. For reports, use account_report mode with report_name and optional date_from/date_to/line_names.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["aggregate", "account_report"], "description": "Analysis mode", "default": "account_report"},
                "model": {"type": "string", "description": "Odoo model (required for aggregate mode)"},
                "domain": {"type": "array", "items": {}, "description": "Filter domain"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to aggregate"},
                "groupby": {"type": "array", "items": {"type": "string"}, "description": "Group-by fields (aggregate mode)"},
                "report_name": {"type": "string", "description": "Report name or alias (e.g. 'Profit and Loss', 'Trial Balance', 'Balance Sheet')"},
                "report_id": {"type": "integer", "description": "Specific Odoo report ID (optional)"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "company_id": {"type": "integer", "description": "Odoo company ID"},
                "line_names": {"type": "array", "items": {"type": "string"}, "description": "Specific line names to filter in the report"},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "odoo_content",
        "display_name": "Odoo Content",
        "description": "Read large text/content/chatter/body fields safely. Modes: metadata, content, thread. Use metadata mode first to find relevant records, then content mode with specific IDs to read full text.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["metadata", "content", "thread"], "description": "Content mode", "default": "metadata"},
                "model": {"type": "string", "description": "Odoo model name"},
                "purpose": {"type": "string", "description": "Short reason why content is needed"},
                "domain": {"type": "array", "items": {}, "description": "Filter domain"},
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific record IDs to read content from"},
                "limit": {"type": "integer", "description": "Max records (default 20)", "default": 20},
                "content_fields": {"type": "array", "items": {"type": "string"}, "description": "Content fields to read (e.g. body, note, description)"},
                "raw_html": {"type": "boolean", "description": "Return raw HTML instead of sanitized", "default": False},
            },
            "required": ["model", "purpose"],
        },
    },
    {
        "name": "odoo_attachment",
        "display_name": "Odoo Attachment",
        "description": "Read Odoo attachment metadata, links, text, OCR, base64, or analysis. Modes: metadata, link, text, base64, analyze. Use odoo_query on ir.attachment for discovery first.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "integer", "description": "Single attachment ID"},
                "attachment_ids": {"type": "array", "items": {"type": "integer"}, "description": "Multiple attachment IDs"},
                "mode": {"type": "string", "enum": ["metadata", "link", "text", "base64", "analyze"], "description": "Attachment mode", "default": "metadata"},
                "max_text_chars": {"type": "integer", "description": "Max characters for text extraction", "default": 10000},
            },
        },
    },
    {
        "name": "odoo_mutation",
        "display_name": "Odoo Mutation",
        "description": "Structured write/workflow tool. Operations: create, write, delete, workflow. Delete and workflow default to dry_run=true. Verifies after create/write/workflow unless disabled.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["create", "write", "delete", "workflow"], "description": "Mutation operation", "default": "create"},
                "model": {"type": "string", "description": "Odoo model name"},
                "record_ids": {"type": "array", "items": {"type": "integer"}, "description": "Record IDs (required for write/delete/workflow)"},
                "values": {"type": "object", "description": "Field values for create/write"},
                "workflow_method": {"type": "string", "description": "Workflow method (e.g. action_confirm, action_done, button_validate)"},
                "dry_run": {"type": "boolean", "description": "Simulate without executing (default false, but true for delete/workflow)", "default": False},
                "verify": {"type": "boolean", "description": "Verify after operation", "default": True},
            },
            "required": ["operation", "model"],
        },
    },
    {
        "name": "odoo_message",
        "display_name": "Odoo Message",
        "description": "Post or update Odoo chatter/Discuss messages. Operations: post, update. Targets: record_chatter, discuss_channel. Safely converts plain text to Odoo HTML.",
        "target_system": "odoo",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["post", "update"], "description": "Message operation", "default": "post"},
                "target_type": {"type": "string", "enum": ["record_chatter", "discuss_channel", "message"], "description": "Target type", "default": "record_chatter"},
                "body": {"type": "string", "description": "Message body text"},
                "model": {"type": "string", "description": "Odoo model (required for record_chatter)"},
                "record_id": {"type": "integer", "description": "Record ID (required for record_chatter)"},
                "channel_id": {"type": "integer", "description": "Discuss channel ID (required for discuss_channel)"},
                "message_id": {"type": "integer", "description": "Message ID (required for update)"},
                "message_type": {"type": "string", "description": "Message type: comment, notification", "default": "comment"},
                "partner_ids": {"type": "array", "items": {"type": "integer"}, "description": "Partner IDs to notify"},
                "attachment_ids": {"type": "array", "items": {"type": "integer"}, "description": "Attachment IDs to include"},
            },
            "required": ["operation", "body"],
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
