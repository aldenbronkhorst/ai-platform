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


@pytest.mark.asyncio
async def test_workspace_odoo_helper_blocks_write_methods():
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

    assert result["status"] == "failed"
    assert result["exit_code"] != 0
    assert "read-oriented methods only" in result["stderr"]
    assert calls == []


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
