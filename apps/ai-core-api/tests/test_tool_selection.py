import uuid

import pytest

from app.models.models import AITool
from app.services.tool_selection import get_tool_selection
from app.services.tool_registry import (
    CONNECTOR_TOOLS_BY_SYSTEM,
    is_model_facing_tool,
)


def _tool(name: str, target_system: str, status: str = "active") -> AITool:
    return AITool(
        id=uuid.uuid4(),
        name=name,
        display_name=name,
        target_system=target_system,
        status=status,
        input_schema={"type": "object"},
    )


class FakeDb:
    def __init__(self, tools: list[AITool]):
        self.tools = tools

    async def execute(self, _stmt):
        tools = self.tools

        class Result:
            def scalars(self):
                return self

            def all(self):
                return tools

        return Result()


def test_connector_tool_registry_keeps_odoo_broker_only():
    assert CONNECTOR_TOOLS_BY_SYSTEM == {"odoo": frozenset()}
    assert not is_model_facing_tool("odoo", "odoo")
    assert not is_model_facing_tool("odoo_query", "odoo")
    assert not is_model_facing_tool("runner.run_python", "runner")
    assert not is_model_facing_tool("ai.save_artifact", "ai-platform")


@pytest.mark.asyncio
async def test_tool_selection_exposes_workspace_not_connector_tool_for_meta_followup():
    tools = [
        _tool("workspace", "ai-platform"),
        _tool("odoo", "odoo"),
        _tool("odoo_query", "odoo"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "did you read the value straight from the report or calc yourself?",
        connected_systems={"odoo"},
    )

    assert [tool.name for tool in result.selected] == ["workspace"]
    assert result.excluded == []
    assert result.intent == "odoo,ai-platform"
    assert result.selection_reason == "model_facing_tools_available"


@pytest.mark.asyncio
async def test_tool_selection_exposes_workspace_for_connected_system_request():
    tools = [
        _tool("workspace", "ai-platform"),
        _tool("odoo", "odoo"),
        _tool("odoo_query", "odoo"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "in odoo what is the total shown on a system report for last month?",
        connected_systems={"odoo"},
    )

    assert [tool.name for tool in result.selected] == ["workspace"]
    assert result.excluded == []
    assert result.intent == "odoo,ai-platform"
    assert result.selection_reason == "model_facing_tools_available"


@pytest.mark.asyncio
async def test_tool_selection_ignores_inactive_and_legacy_tools():
    tools = [
        _tool("workspace", "ai-platform"),
        _tool("odoo", "odoo", status="inactive"),
        _tool("odoo_query", "odoo"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "hi",
        connected_systems={"odoo"},
    )

    assert [tool.name for tool in result.selected] == ["workspace"]
    assert result.selection_reason == "model_facing_tools_available"


@pytest.mark.asyncio
async def test_tool_selection_selects_document_reader_for_uploaded_pdf_without_connectors():
    tools = [
        _tool("workspace", "ai-platform"),
        _tool("document_reader", "ai-platform"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "Please read the uploaded PDF.\n\n[Attached file context]\nFile: agreement.pdf (application/pdf, id=abc)",
        connected_systems=set(),
    )

    assert [tool.name for tool in result.selected] == ["workspace", "document_reader"]
    assert result.intent == "ai-platform"
    assert result.selection_reason == "model_facing_tools_available"


@pytest.mark.asyncio
async def test_tool_selection_exposes_workspace_without_connectors():
    tools = [
        _tool("workspace", "ai-platform"),
        _tool("odoo", "odoo"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "Calculate this in a quick script",
        connected_systems=set(),
    )

    assert [tool.name for tool in result.selected] == ["workspace"]
    assert result.intent == "ai-platform"
