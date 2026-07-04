import asyncio

import pytest

from app.services.agent_tool_executor import execute_model_tool_calls


def _tool_call(call_id: str, name: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


@pytest.mark.asyncio
async def test_execute_model_tool_calls_runs_independent_tools_concurrently_in_order():
    active = 0
    max_active = 0

    async def run_tool(name, _arguments):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02 if name == "first" else 0.01)
        active -= 1
        return {"name": name}

    results = await execute_model_tool_calls(
        [_tool_call("call_1", "first"), _tool_call("call_2", "second")],
        exposed_tool_names={"first", "second"},
        run_tool=run_tool,
        compact_result=lambda value: value,
    )

    assert max_active == 2
    assert [result.tool_call_id for result in results] == ["call_1", "call_2"]
    assert [result.raw_result["name"] for result in results] == ["first", "second"]


@pytest.mark.asyncio
async def test_execute_model_tool_calls_keeps_serial_tools_sequential():
    active = 0
    max_active = 0
    order = []

    async def run_tool(name, _arguments):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(name)
        await asyncio.sleep(0.01)
        active -= 1
        return {"name": name}

    results = await execute_model_tool_calls(
        [_tool_call("call_1", "workspace"), _tool_call("call_2", "workspace")],
        exposed_tool_names={"workspace"},
        serial_tool_names={"workspace"},
        run_tool=run_tool,
        compact_result=lambda value: value,
    )

    assert max_active == 1
    assert order == ["workspace", "workspace"]
    assert [result.tool_call_id for result in results] == ["call_1", "call_2"]
