import uuid

import pytest
from unittest.mock import AsyncMock

from app.services.model_router import _execute_tool_call_impl
from app.services.workspace_runtime import run_workspace


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
async def test_workspace_odoo_helper_brokers_read_call_without_credentials():
    async def fake_odoo(model, method, args, kwargs):
        assert model == "account.move"
        assert method == "search_count"
        assert args == [[["move_type", "=", "out_refund"]]]
        assert kwargs == {}
        return {"model": model, "method": method, "result": 7}

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_odoo import execute_kw\n"
                "print(execute_kw('account.move', 'search_count', [[['move_type', '=', 'out_refund']]]))"
            ),
            "timeout": 10,
        },
        odoo_executor=fake_odoo,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "7"
    assert result["odoo_calls"] == 1
    assert result["tool_calls"] == 1
    assert result["connector_calls"] == {"odoo": 1}


@pytest.mark.asyncio
async def test_workspace_odoo_call_helper_accepts_natural_search_read_kwargs():
    async def fake_odoo(model, method, args, kwargs):
        assert model == "account.move"
        assert method == "search_read"
        assert args == [[["move_type", "=", "out_refund"]]]
        assert kwargs == {"fields": ["id", "name"], "limit": 500}
        return {"model": model, "method": method, "result": [{"id": 57508, "name": "RINV-2026-00007"}]}

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_odoo import call as odoo_call\n"
                "rows = odoo_call(\n"
                "    'account.move',\n"
                "    'search_read',\n"
                "    domain=[['move_type', '=', 'out_refund']],\n"
                "    fields=['id', 'name'],\n"
                "    limit=500,\n"
                ")\n"
                "print(rows[0]['name'])"
            ),
            "timeout": 10,
        },
        odoo_executor=fake_odoo,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "RINV-2026-00007"
    assert result["connector_calls"] == {"odoo": 1}


@pytest.mark.asyncio
async def test_workspace_odoo_call_helper_unwraps_raw_payload_result():
    async def fake_odoo(model, method, args, kwargs):
        assert model == "account.move"
        assert method == "search_count"
        assert args == [[]]
        assert kwargs == {}
        return {"model": model, "method": method, "transport": "jsonrpc", "result": 408}

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_odoo import call\n"
                "print(call({'model': 'account.move', 'method': 'search_count', 'args': [[]]}))"
            ),
            "timeout": 10,
        },
        odoo_executor=fake_odoo,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "408"
    assert result["connector_calls"] == {"odoo": 1}


@pytest.mark.asyncio
async def test_workspace_odoo_helpers_make_bulk_attachment_pattern_easy():
    calls = []

    async def fake_odoo(model, method, args, kwargs):
        calls.append((model, method, args, kwargs))
        if model == "account.move":
            return {"result": [{"id": 1}, {"id": 2}]}
        if model == "ir.attachment":
            return {"result": [{"res_id": 1, "name": "one.pdf"}, {"res_id": 2, "name": "two.pdf"}]}
        raise AssertionError(model)

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_odoo import search_read\n"
                "notes = search_read('account.move', [['move_type', '=', 'out_refund']], fields=['id'])\n"
                "ids = [row['id'] for row in notes]\n"
                "attachments = search_read('ir.attachment', [['res_model', '=', 'account.move'], ['res_id', 'in', ids]], fields=['res_id', 'name'])\n"
                "print(len(notes), len(attachments))"
            ),
            "timeout": 10,
        },
        odoo_executor=fake_odoo,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "2 2"
    assert result["connector_calls"] == {"odoo": 2}
    assert calls == [
        ("account.move", "search_read", [[["move_type", "=", "out_refund"]]], {"fields": ["id"]}),
        (
            "ir.attachment",
            "search_read",
            [[["res_model", "=", "account.move"], ["res_id", "in", [1, 2]]]],
            {"fields": ["res_id", "name"]},
        ),
    ]


@pytest.mark.asyncio
async def test_workspace_odoo_helper_allows_write_methods_to_reach_connector():
    calls = []

    async def fake_odoo(model, method, args, kwargs):
        calls.append((model, method, args, kwargs))
        return {"result": True}

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_odoo import execute_kw\n"
                "execute_kw('res.partner', 'write', [[[1], {'name': 'bad'}]])"
            ),
            "timeout": 10,
        },
        odoo_executor=fake_odoo,
    )

    assert result["status"] == "success"
    assert result["stdout"] == ""
    assert calls == [("res.partner", "write", [[[1], {"name": "bad"}]], {})]


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
