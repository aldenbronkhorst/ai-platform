import uuid

import pytest

from app.models.models import AITool
from app.services.tool_selection import get_tool_selection
from app.services.tool_registry import (
    CONNECTOR_TOOLS_BY_SYSTEM,
    MICROSOFT_NATIVE_CONNECTOR_SYSTEMS,
    MICROSOFT_NATIVE_TOOL_NAMES,
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


def _microsoft_tools() -> list[AITool]:
    tools: list[AITool] = []
    for system in MICROSOFT_NATIVE_CONNECTOR_SYSTEMS:
        tools.extend(_tool(name, system) for name in sorted(CONNECTOR_TOOLS_BY_SYSTEM[system]))
    return tools


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


def test_connector_tool_registry_exposes_canonical_model_facing_tools():
    assert is_model_facing_tool("odoo_ops_runner", "odoo")
    assert set(MICROSOFT_NATIVE_TOOL_NAMES) == {
        "ms_graph",
        "ms_exchange_powershell",
        "ms_teams_powershell",
        "ms_sharepoint_pnp_powershell",
        "ms_azure_cli",
    }
    for system in MICROSOFT_NATIVE_CONNECTOR_SYSTEMS:
        for tool_name in CONNECTOR_TOOLS_BY_SYSTEM[system]:
            assert is_model_facing_tool(tool_name, system)
    assert is_model_facing_tool("github_cli", "github")
    assert not is_model_facing_tool("odoo_query", "odoo")
    assert not is_model_facing_tool("ms_admin", "microsoft_graph")
    assert not is_model_facing_tool("azure_cli", "azure_cli")
    assert not is_model_facing_tool("github_pr_tool", "github")


@pytest.mark.asyncio
async def test_tool_selection_exposes_connected_tools_without_keyword_intent():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        _tool("odoo_query", "odoo"),
        *_microsoft_tools(),
        _tool("github_cli", "github"),
        _tool("github_pr_tool", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "yes go ahead",
        connected_systems={"odoo", "microsoft_graph", "github"},
    )

    assert {tool.name for tool in result.selected} == {"odoo_ops_runner", "ms_graph", "github_cli"}
    assert result.excluded == []
    assert result.intent == "github,microsoft_graph,odoo"
    assert result.selection_reason == "connected_tools_available"


@pytest.mark.asyncio
async def test_tool_selection_does_not_expose_unconnected_tools():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        *_microsoft_tools(),
        _tool("github_cli", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "check Azure resources",
        connected_systems={"odoo"},
    )

    assert [tool.name for tool in result.selected] == ["odoo_ops_runner"]
    assert result.intent == "odoo"


@pytest.mark.asyncio
async def test_tool_selection_ignores_inactive_and_legacy_tools():
    tools = [
        _tool("odoo_ops_runner", "odoo", status="inactive"),
        _tool("odoo_query", "odoo"),
        _tool("github_cli", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "hi",
        connected_systems={"odoo", "github"},
    )

    assert [tool.name for tool in result.selected] == ["github_cli"]


@pytest.mark.asyncio
async def test_tool_selection_selects_document_reader_for_uploaded_pdf_without_connectors():
    tools = [
        _tool("document_reader", "ai-platform"),
        _tool("ms_azure_cli", "azure_cli"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "Please read the uploaded PDF.\n\n[Attached file context]\nFile: agreement.pdf (application/pdf, id=abc)",
        connected_systems=set(),
    )

    assert [tool.name for tool in result.selected] == ["document_reader"]
    assert result.intent == "ai-platform"
    assert result.selection_reason == "connected_tools_available"
