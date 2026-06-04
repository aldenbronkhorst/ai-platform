import uuid

import pytest

from app.models.models import AITool
from app.services.tool_selection import get_tool_selection
from app.services.tool_registry import is_model_facing_tool


def _tool(name: str, target_system: str) -> AITool:
    return AITool(
        id=uuid.uuid4(),
        name=name,
        display_name=name,
        target_system=target_system,
        status="active",
        input_schema={"type": "object"},
    )


def test_connector_tool_registry_exposes_one_tool_per_system():
    assert is_model_facing_tool("odoo_ops_runner", "odoo")
    assert is_model_facing_tool("azure_cli", "azure")
    assert is_model_facing_tool("github_cli", "github")
    assert not is_model_facing_tool("odoo_query", "odoo")
    assert not is_model_facing_tool("azure_logs_tool", "azure")
    assert not is_model_facing_tool("github_pr_tool", "github")
    assert is_model_facing_tool("internal_ai_platform_tool", "ai-platform")


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


@pytest.mark.asyncio
async def test_tool_selection_skips_tools_without_connector_intent():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        _tool("odoo_query", "odoo"),
        _tool("azure_cli", "azure"),
        _tool("azure_logs_tool", "azure"),
        _tool("github_cli", "github"),
        _tool("github_pr_tool", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "hi there",
        connected_systems={"odoo", "azure", "github"},
    )

    assert result.selected == []
    assert {tool.name for tool in result.excluded} == {"odoo_ops_runner", "azure_cli", "github_cli"}
    assert result.intent == "no_connector_intent"


@pytest.mark.asyncio
async def test_tool_selection_selects_only_matching_connected_system():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        _tool("odoo_report", "odoo"),
        _tool("azure_cli", "azure"),
        _tool("azure_revision_tool", "azure"),
        _tool("github_cli", "github"),
        _tool("github_actions_tool", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "List my Azure container app revisions and logs",
        connected_systems={"odoo", "azure", "github"},
    )

    assert [tool.name for tool in result.selected] == ["azure_cli"]
    assert {tool.name for tool in result.excluded} == {"github_cli", "odoo_ops_runner"}
    assert result.intent == "azure"
    assert result.selection_reason == "message_intent_matched_connected_systems"


@pytest.mark.asyncio
async def test_tool_selection_does_not_select_unconnected_matching_system():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        _tool("azure_cli", "azure"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "Can you check Azure resources?",
        connected_systems={"odoo"},
    )

    assert result.selected == []
    assert result.intent == "no_connector_intent"
