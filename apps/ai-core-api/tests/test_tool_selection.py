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


MICROSOFT_TOOL_NAMES = tuple(sorted(MICROSOFT_NATIVE_TOOL_NAMES))
ALL_MICROSOFT_SYSTEMS = set(MICROSOFT_NATIVE_CONNECTOR_SYSTEMS)


def _tool(name: str, target_system: str) -> AITool:
    return AITool(
        id=uuid.uuid4(),
        name=name,
        display_name=name,
        target_system=target_system,
        status="active",
        input_schema={"type": "object"},
    )


def _microsoft_tools() -> list[AITool]:
    tools: list[AITool] = []
    for system in MICROSOFT_NATIVE_CONNECTOR_SYSTEMS:
        tools.extend(_tool(name, system) for name in sorted(CONNECTOR_TOOLS_BY_SYSTEM[system]))
    return tools


def test_connector_tool_registry_exposes_canonical_model_facing_tools():
    assert is_model_facing_tool("odoo_ops_runner", "odoo")
    assert set(MICROSOFT_TOOL_NAMES) == {
        "ms_graph",
        "ms_graph_powershell",
        "ms_exchange_powershell",
        "ms_teams_powershell",
        "ms_sharepoint_pnp_powershell",
        "ms_az_powershell",
        "ms_azure_cli",
        "ms_bicep",
    }
    for system in MICROSOFT_NATIVE_CONNECTOR_SYSTEMS:
        for tool_name in CONNECTOR_TOOLS_BY_SYSTEM[system]:
            assert is_model_facing_tool(tool_name, system)
    assert is_model_facing_tool("github_cli", "github")
    assert not is_model_facing_tool("odoo_query", "odoo")
    assert not is_model_facing_tool("ms_admin", "microsoft_graph")
    assert not is_model_facing_tool("ms_powershell", "microsoft_graph")
    assert not is_model_facing_tool("azure_cli", "azure_cli")
    assert not is_model_facing_tool("azure_logs_tool", "azure_cli")
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
        *_microsoft_tools(),
        _tool("azure_logs_tool", "azure_cli"),
        _tool("github_cli", "github"),
        _tool("github_pr_tool", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "hi there",
        connected_systems={"odoo", *ALL_MICROSOFT_SYSTEMS, "github"},
    )

    assert result.selected == []
    assert {tool.name for tool in result.excluded} == {
        "odoo_ops_runner",
        "github_cli",
    } | set(MICROSOFT_TOOL_NAMES)
    assert result.intent == "no_connector_intent"


@pytest.mark.asyncio
async def test_tool_selection_selects_only_matching_connected_system():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        _tool("odoo_report", "odoo"),
        *_microsoft_tools(),
        _tool("azure_revision_tool", "azure_cli"),
        _tool("github_cli", "github"),
        _tool("github_actions_tool", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "List my Azure container app revisions and logs",
        connected_systems={"odoo", "azure_cli", "github"},
    )

    assert [tool.name for tool in result.selected] == ["ms_azure_cli"]
    assert {tool.name for tool in result.excluded} == {"github_cli", "odoo_ops_runner", "ms_az_powershell", "ms_bicep"}
    assert result.intent == "azure_cli"
    assert result.selection_reason == "message_intent_matched_connected_systems"


@pytest.mark.asyncio
async def test_tool_selection_does_not_select_unconnected_matching_system():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        *_microsoft_tools(),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "Can you check Azure resources?",
        connected_systems={"odoo"},
    )

    assert result.selected == []
    assert result.intent == "no_connector_intent"


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
    assert result.excluded == []
    assert result.intent == "ai-platform"
    assert result.selection_reason == "message_intent_matched_available_tools"


@pytest.mark.asyncio
async def test_tool_selection_selects_native_microsoft_tools_for_exchange_intune_terms():
    tools = [
        *_microsoft_tools(),
        _tool("github_cli", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "Check Exchange Online mailbox permissions and Intune managed devices",
        connected_systems={"microsoft_graph", "exchange_online", "github"},
    )

    assert {tool.name for tool in result.selected} == {"ms_graph", "ms_exchange_powershell"}
    assert {tool.name for tool in result.excluded} == {"github_cli", "ms_graph_powershell"}
    assert result.intent == "exchange_online,microsoft_graph"


@pytest.mark.asyncio
async def test_tool_selection_selects_microsoft_admin_for_cross_system_user_creation():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        *_microsoft_tools(),
        _tool("github_cli", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "create a microsoft uerer for employee gerhard in odoo",
        connected_systems={"odoo", "microsoft_graph", "github"},
    )

    assert {tool.name for tool in result.selected} == {"odoo_ops_runner", "ms_graph"}
    assert {tool.name for tool in result.excluded} == {"github_cli", "ms_graph_powershell"}
    assert result.intent == "microsoft_graph,odoo"
    assert result.selection_reason == "message_intent_matched_connected_systems"


@pytest.mark.asyncio
async def test_tool_selection_treats_ms_admin_abbreviation_as_microsoft_with_admin_context():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        *_microsoft_tools(),
        _tool("github_cli", "github"),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "add gerhard from odoo as a user in ms as gw.c@",
        connected_systems={"odoo", "microsoft_graph", "github"},
    )

    assert {tool.name for tool in result.selected} == {"odoo_ops_runner", "ms_graph"}
    assert {tool.name for tool in result.excluded} == {"github_cli", "ms_graph_powershell"}
    assert result.intent == "microsoft_graph,odoo"


@pytest.mark.asyncio
async def test_tool_selection_does_not_treat_plain_ms_units_as_microsoft_admin():
    tools = [*_microsoft_tools()]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "the request took 5 ms",
        connected_systems={"microsoft_graph"},
    )

    assert result.selected == []
    assert result.intent == "no_connector_intent"


@pytest.mark.asyncio
async def test_tool_selection_keeps_odoo_active_users_scoped_to_odoo():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        *_microsoft_tools(),
    ]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "show active users in odoo",
        connected_systems={"odoo", *ALL_MICROSOFT_SYSTEMS},
    )

    assert [tool.name for tool in result.selected] == ["odoo_ops_runner"]
    assert {tool.name for tool in result.excluded} == set(MICROSOFT_TOOL_NAMES)
    assert result.intent == "odoo"
