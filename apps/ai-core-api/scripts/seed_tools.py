import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, update
from app.core.config import get_settings
from app.models.models import AITool
from app.services.tool_definitions import CANONICAL_TOOL_DEFINITIONS
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, CONNECTOR_SYSTEMS

settings = get_settings()

STALE_CONNECTOR_TARGET_SYSTEMS = {"azure"}


async def seed_tools():
    database_url = settings.database_url
    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        for tool_data in CANONICAL_TOOL_DEFINITIONS:
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

        await session.execute(
            update(AITool)
            .where(
                AITool.status == "active",
                AITool.target_system.in_(STALE_CONNECTOR_TARGET_SYSTEMS),
            )
            .values(status="archived", updated_at=datetime.now(timezone.utc))
        )
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
