import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import get_settings
from app.models.models import AITool
from app.schemas.schemas import AIToolCreate

settings = get_settings()

TOOLS = [
    {
        "name": "odoo.search_read",
        "display_name": "Odoo Search Read",
        "description": "Search and read records from Odoo",
        "target_system": "odoo",
        "input_schema": {"model": "string", "domain": "list", "fields": "list"},
        "output_schema": {"records": "list"},
    },
    {
        "name": "odoo.execute_kw",
        "display_name": "Odoo Execute KW",
        "description": "Execute any Odoo model method",
        "target_system": "odoo",
        "input_schema": {"model": "string", "method": "string", "args": "list", "kwargs": "dict"},
        "output_schema": {"result": "any"},
    },
    {
        "name": "odoo.attachment_ocr",
        "display_name": "Odoo Attachment OCR",
        "description": "OCR an attachment from Odoo",
        "target_system": "odoo",
        "input_schema": {"attachment_id": "integer"},
        "output_schema": {"text": "string"},
    },
    {
        "name": "odoo.attach_artifact",
        "display_name": "Odoo Attach Artifact",
        "description": "Attach a file to an Odoo record",
        "target_system": "odoo",
        "input_schema": {"model": "string", "record_id": "integer", "artifact_id": "string"},
        "output_schema": {"attachment_id": "integer"},
    },
    {
        "name": "github.create_pr",
        "display_name": "GitHub Create PR",
        "description": "Create a pull request on GitHub",
        "target_system": "github",
        "input_schema": {"repo": "string", "title": "string", "body": "string", "head": "string", "base": "string"},
        "output_schema": {"pr_url": "string"},
    },
    {
        "name": "github.search_repo",
        "display_name": "GitHub Search Repo",
        "description": "Search within a GitHub repository",
        "target_system": "github",
        "input_schema": {"repo": "string", "query": "string"},
        "output_schema": {"results": "list"},
    },
    {
        "name": "runner.run_python",
        "display_name": "Runner Run Python",
        "description": "Run a Python script in a secure runner",
        "target_system": "runner",
        "input_schema": {"script": "string", "inputs": "dict"},
        "output_schema": {"stdout": "string", "stderr": "string", "artifacts": "list"},
    },
    {
        "name": "ai.save_artifact",
        "display_name": "AI Save Artifact",
        "description": "Save an artifact to AI Platform storage",
        "target_system": "ai-platform",
        "input_schema": {"content": "bytes", "filename": "string", "type": "string"},
        "output_schema": {"artifact_id": "string", "uri": "string"},
    },
    {
        "name": "ai.create_task",
        "display_name": "AI Create Task",
        "description": "Create a task in the AI Platform",
        "target_system": "ai-platform",
        "input_schema": {"title": "string", "description": "string", "owner": "string"},
        "output_schema": {"task_id": "string"},
    },
]


async def seed_tools():
    engine = create_async_engine(settings.database_url, future=True)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        for tool_data in TOOLS:
            from sqlalchemy import select
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
