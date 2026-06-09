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


def _microsoft_tools() -> list[AITool]:
    return [_tool(name, "azure") for name in ("ms_azure_cli", "ms_graph", "ms_powershell", "ms_bicep")]


def test_connector_tool_registry_exposes_canonical_model_facing_tools():
    assert is_model_facing_tool("odoo_ops_runner", "odoo")
    assert is_model_facing_tool("ms_azure_cli", "azure")
    assert is_model_facing_tool("ms_graph", "azure")
    assert is_model_facing_tool("ms_powershell", "azure")
    assert is_model_facing_tool("ms_bicep", "azure")
    assert is_model_facing_tool("github_cli", "github")
    assert not is_model_facing_tool("odoo_query", "odoo")
    assert not is_model_facing_tool("ms_admin", "azure")
    assert not is_model_facing_tool("azure_cli", "azure")
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
        *_microsoft_tools(),
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
    assert {tool.name for tool in result.excluded} == {
        "odoo_ops_runner",
        "ms_azure_cli",
        "ms_graph",
        "ms_powershell",
        "ms_bicep",
        "github_cli",
    }
    assert result.intent == "no_connector_intent"


@pytest.mark.asyncio
async def test_tool_selection_selects_only_matching_connected_system():
    tools = [
        _tool("odoo_ops_runner", "odoo"),
        _tool("odoo_report", "odoo"),
        *_microsoft_tools(),
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

    assert [tool.name for tool in result.selected] == ["ms_azure_cli"]
    assert {tool.name for tool in result.excluded} == {
        "github_cli",
        "ms_bicep",
        "ms_graph",
        "ms_powershell",
        "odoo_ops_runner",
    }
    assert result.intent == "azure"
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
        _tool("ms_azure_cli", "azure"),
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
        connected_systems={"azure", "github"},
    )

    assert {tool.name for tool in result.selected} == {"ms_graph", "ms_powershell"}
    assert {tool.name for tool in result.excluded} == {"github_cli", "ms_azure_cli", "ms_bicep"}
    assert result.intent == "azure"


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
        connected_systems={"odoo", "azure", "github"},
    )

    assert {tool.name for tool in result.selected} == {"odoo_ops_runner", "ms_graph"}
    assert {tool.name for tool in result.excluded} == {"github_cli", "ms_azure_cli", "ms_bicep", "ms_powershell"}
    assert result.intent == "azure,odoo"
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
        connected_systems={"odoo", "azure", "github"},
    )

    assert {tool.name for tool in result.selected} == {"odoo_ops_runner", "ms_graph"}
    assert {tool.name for tool in result.excluded} == {"github_cli", "ms_azure_cli", "ms_bicep", "ms_powershell"}
    assert result.intent == "azure,odoo"


@pytest.mark.asyncio
async def test_tool_selection_does_not_treat_plain_ms_units_as_microsoft_admin():
    tools = [*_microsoft_tools()]

    result = await get_tool_selection(
        FakeDb(tools),
        uuid.uuid4(),
        "the request took 5 ms",
        connected_systems={"azure"},
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
        connected_systems={"odoo", "azure"},
    )

    assert [tool.name for tool in result.selected] == ["odoo_ops_runner"]
    assert {tool.name for tool in result.excluded} == {"ms_azure_cli", "ms_graph", "ms_powershell", "ms_bicep"}
    assert result.intent == "odoo"
