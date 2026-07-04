import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolRunner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
ToolCompactor = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ExecutedToolCall:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    raw_result: dict[str, Any]
    model_result: dict[str, Any]


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    try:
        parsed = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_tool_call(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    if call.get("type") != "function":
        return None
    function = call.get("function")
    if not isinstance(function, dict):
        return None
    name = str(function.get("name") or "").strip()
    if not name:
        return None
    return (
        str(call.get("id") or ""),
        name,
        _parse_tool_arguments(function.get("arguments")),
    )


def _unavailable_tool_result(name: str) -> dict[str, Any]:
    return {
        "error": True,
        "status": "failed",
        "error_type": "unavailable_tool",
        "message": f"Tool '{name}' is not exposed for this chat turn.",
    }


async def execute_model_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    exposed_tool_names: set[str],
    run_tool: ToolRunner,
    compact_result: ToolCompactor,
    serial_tool_names: set[str] | None = None,
) -> list[ExecutedToolCall]:
    serial_tool_names = serial_tool_names or set()
    parsed_calls: list[tuple[str, str, dict[str, Any]]] = []
    for call in tool_calls:
        parsed = _parse_tool_call(call)
        if parsed is None:
            continue
        parsed_calls.append(parsed)

    async def execute_one(parsed: tuple[str, str, dict[str, Any]]) -> ExecutedToolCall:
        tool_call_id, name, arguments = parsed
        raw_result = (
            await run_tool(name, arguments)
            if name in exposed_tool_names
            else _unavailable_tool_result(name)
        )
        model_result = compact_result(raw_result if isinstance(raw_result, dict) else {"result": raw_result})
        return ExecutedToolCall(
            tool_call_id=tool_call_id,
            tool_name=name,
            arguments=arguments,
            raw_result=raw_result if isinstance(raw_result, dict) else {"result": raw_result},
            model_result=model_result,
        )

    if not parsed_calls:
        return []

    if len(parsed_calls) == 1 or any(name in serial_tool_names for _tool_call_id, name, _arguments in parsed_calls):
        return [await execute_one(parsed) for parsed in parsed_calls]

    return list(await asyncio.gather(*(execute_one(parsed) for parsed in parsed_calls)))
