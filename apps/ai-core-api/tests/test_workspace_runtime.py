import uuid

import pytest
from unittest.mock import AsyncMock

from app.services.model_router import _execute_tool_call_impl
from app.services.workspace_runtime import WorkspaceSession, run_workspace


@pytest.mark.asyncio
async def test_workspace_runs_python_and_collects_files():
    result = await run_workspace({
        "code": "print('hello workspace')\nopen('answer.txt', 'w').write('42')",
        "timeout": 10,
    })

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello workspace"
    assert result["stderr"] == ""
    assert result["files"] == [
        {
            "path": "answer.txt",
            "bytes": 2,
            "sha256": "73475cb40a568e8da8a045ced110137e159f890ac4da883b6b17dc651b3a8049",
            "preview": "42",
        }
    ]


@pytest.mark.asyncio
async def test_workspace_does_not_expose_final_answer_helper():
    result = await run_workspace({
        "code": "from ai_platform_tools import final\nfinal('done')",
        "timeout": 10,
    })

    assert result["status"] == "failed"
    assert "ImportError" in result["stderr"]
    assert "final_answer" not in result


@pytest.mark.asyncio
async def test_workspace_can_call_odoo_raw_connector_without_credentials():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        assert tool_name == "odoo"
        return 7

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_tools import call\n"
                "response = call('odoo', {\n"
                "    'model': 'account.move',\n"
                "    'method': 'search_count',\n"
                "    'args': [[['move_type', '=', 'out_refund']]],\n"
                "    'kwargs': {},\n"
                "})\n"
                "print(response)"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "7"
    assert result["odoo_calls"] == 1
    assert result["tool_calls"] == 1
    assert result["connector_calls"] == {"odoo": 1}
    assert calls == [
        (
            "odoo",
            {
                "model": "account.move",
                "method": "search_count",
                "args": [[["move_type", "=", "out_refund"]]],
                "kwargs": {},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_workspace_python_has_call_available_by_default():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        return 11

    result = await run_workspace(
        {
            "code": "response = call('odoo', {'model': 'res.partner', 'method': 'search_count', 'args': [[]]})\nprint(response)",
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "11"
    assert result["odoo_calls"] == 1
    assert calls == [
        (
            "odoo",
            {"model": "res.partner", "method": "search_count", "args": [[]]},
        ),
    ]


@pytest.mark.asyncio
async def test_workspace_call_returns_connector_error_without_failing_script():
    async def fake_tool(tool_name, arguments):
        return {
            "error": True,
            "error_type": "odoo_error",
            "message": "Invalid field 'missing_field' on model 'res.partner'.",
        }

    result = await run_workspace(
        {
            "code": (
                "response = call('odoo', {'model': 'res.partner', 'method': 'read'})\n"
                "print(response['error'], response['error_type'])"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "True odoo_error"
    assert result["stderr"] == ""
    assert result["connector_calls"] == {"odoo": 1}
    assert result["connector_error_calls"] == {"odoo": 1}


@pytest.mark.asyncio
async def test_workspace_call_checked_keeps_explicit_exception_behavior():
    async def fake_tool(tool_name, arguments):
        return {
            "error": True,
            "error_type": "odoo_error",
            "message": "Invalid field 'missing_field' on model 'res.partner'.",
        }

    result = await run_workspace(
        {
            "code": "call_checked('odoo', {'model': 'res.partner', 'method': 'read'})",
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "failed"
    assert "PlatformToolError" in result["stderr"]
    assert "Invalid field" in result["stderr"]


@pytest.mark.asyncio
async def test_workspace_uses_raw_odoo_rpc_payloads():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        assert tool_name == "odoo"
        return [{"id": 7, "name": "Example Partner"}]

    result = await run_workspace(
        {
            "code": (
                "partners = call('odoo', {\n"
                "    'model': 'res.partner',\n"
                "    'method': 'search_read',\n"
                "    'args': [[['name', 'ilike', 'Example']]],\n"
                "    'kwargs': {'fields': ['id', 'name'], 'limit': 1},\n"
                "})\n"
                "print(partners[0]['id'], partners[0]['name'])"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "7 Example Partner"
    assert result["odoo_calls"] == 1
    assert calls == [
        (
            "odoo",
            {
                "model": "res.partner",
                "method": "search_read",
                "args": [[["name", "ilike", "Example"]]],
                "kwargs": {"fields": ["id", "name"], "limit": 1},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_workspace_can_bulk_odoo_through_raw_connector():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        assert tool_name == "odoo"
        return {
            "results": [
                {"name": "notes", "result": [{"id": 1}, {"id": 2}]},
                {"name": "attachments", "result": [{"res_id": 1, "name": "one.pdf"}, {"res_id": 2, "name": "two.pdf"}]},
            ],
            "count": 2,
        }

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_tools import call\n"
                "payload = {'calls': [\n"
                "    {'name': 'notes', 'model': 'account.move', 'method': 'search_read', 'args': [[['move_type', '=', 'out_refund']]], 'kwargs': {'fields': ['id']}},\n"
                "    {'name': 'attachments', 'model': 'ir.attachment', 'method': 'search_read', 'args': [[['res_model', '=', 'account.move'], ['res_id', 'in', [1, 2]]]], 'kwargs': {'fields': ['res_id', 'name']}},\n"
                "]}\n"
                "response = call('odoo', payload)\n"
                "notes = response['results'][0]['result']\n"
                "attachments = response['results'][1]['result']\n"
                "print(len(notes), len(attachments))"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "2 2"
    assert result["connector_calls"] == {"odoo": 1}
    assert calls[0][0] == "odoo"
    assert calls[0][1]["calls"][0]["model"] == "account.move"
    assert calls[0][1]["calls"][1]["model"] == "ir.attachment"


@pytest.mark.asyncio
async def test_workspace_python_can_call_any_platform_tool():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"status": "success", "connector": tool_name, "value": arguments["value"]}

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_tools import call\n"
                "print(call('github_cli', {'value': 42})['value'])"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "42"
    assert result["connector_calls"] == {"github_cli": 1}
    assert calls == [("github_cli", {"value": 42})]


@pytest.mark.asyncio
async def test_workspace_shell_can_call_any_platform_tool():
    async def fake_tool(tool_name, arguments):
        return {"status": "success", "connector": tool_name, "value": arguments["value"]}

    result = await run_workspace(
        {
            "language": "shell",
            "code": "ai-platform-tool github_cli '{\"value\": 42}'",
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert '"value": 42' in result["stdout"]
    assert result["connector_calls"] == {"github_cli": 1}


@pytest.mark.asyncio
async def test_workspace_shell_command_fails_on_connector_error():
    async def fake_tool(tool_name, arguments):
        return {
            "error": True,
            "error_type": "connector_error",
            "message": "Connector failed.",
        }

    result = await run_workspace(
        {
            "language": "shell",
            "code": "ai-platform-tool odoo '{\"model\": \"res.partner\", \"method\": \"read\"}'",
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "failed"
    assert '"error": true' in result["stderr"]
    assert "Connector failed." in result["stderr"]


@pytest.mark.asyncio
async def test_workspace_runs_shell_and_collects_files():
    result = await run_workspace({
        "language": "terminal",
        "code": "printf 'terminal-ok\\n'\nprintf 123 > terminal.txt",
        "timeout": 10,
    })

    assert result["status"] == "success"
    assert result["language"] == "shell"
    assert result["stdout"].strip() == "terminal-ok"
    assert result["files"] == [
        {
            "path": "terminal.txt",
            "bytes": 3,
            "sha256": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
            "preview": "123",
        }
    ]


@pytest.mark.asyncio
async def test_workspace_session_reuses_files_between_runs():
    async with WorkspaceSession() as session:
        first = await session.run({
            "code": "open('state.txt', 'w', encoding='utf-8').write('42')",
            "timeout": 10,
        })
        second = await session.run({
            "code": "print(open('state.txt', encoding='utf-8').read())",
            "timeout": 10,
        })

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert first["workspace_id"] == second["workspace_id"]
    assert first["run_index"] == 1
    assert second["run_index"] == 2
    assert second["stdout"].strip() == "42"


@pytest.mark.asyncio
async def test_workspace_session_reports_per_run_connector_calls():
    async def fake_tool(tool_name, arguments):
        return {"status": "success", "tool": tool_name, "value": arguments["value"]}

    async with WorkspaceSession(tool_executor=fake_tool) as session:
        first = await session.run({
            "code": "print(call('odoo', {'value': 1})['value'])",
            "timeout": 10,
        })
        second = await session.run({
            "code": "print(call('odoo', {'value': 2})['value'])",
            "timeout": 10,
        })

    assert first["connector_calls"] == {"odoo": 1}
    assert first["connector_calls_total"] == {"odoo": 1}
    assert first["connector_error_calls"] == {}
    assert second["connector_calls"] == {"odoo": 1}
    assert second["connector_calls_total"] == {"odoo": 2}
    assert second["connector_error_calls"] == {}


@pytest.mark.asyncio
async def test_workspace_rejects_empty_code():
    result = await run_workspace({"code": ""})

    assert result["status"] == "failed"
    assert result["error_type"] == "invalid_workspace_arguments"


@pytest.mark.asyncio
async def test_model_router_dispatches_workspace_tool():
    result = await _execute_tool_call_impl(
        AsyncMock(),
        uuid.uuid4(),
        "workspace",
        {"code": "print(2 + 3)", "timeout": 10, "purpose": "test arithmetic"},
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "5"
