import uuid

import pytest
from unittest.mock import AsyncMock

from app.services.model_router import _execute_tool_call_impl
from app.services.workspace_runtime import WorkspaceSession, WorkspaceToolBroker, run_workspace


@pytest.mark.asyncio
async def test_workspace_runs_python_and_collects_files():
    result = await run_workspace({
        "code": "print('hello workspace')\nopen(output_path('answer.txt'), 'w').write('42')",
        "timeout": 10,
    })

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello workspace"
    assert result["stderr"] == ""
    assert result["files"] == [
        {
            "path": "outputs/answer.txt",
            "bytes": 2,
            "mime_type": "text/plain",
            "sha256": "73475cb40a568e8da8a045ced110137e159f890ac4da883b6b17dc651b3a8049",
            "content_base64": "NDI=",
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
async def test_workspace_call_raises_connector_errors():
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

    assert result["status"] == "failed"
    assert "PlatformToolError" in result["stderr"]
    assert "Invalid field" in result["stderr"]
    assert result["connector_calls"] == {"odoo": 1}
    assert result["connector_error_calls"] == {"odoo": 1}
    assert result["connector_error_details"] == [{
        "tool_name": "odoo",
        "error_type": "odoo_error",
        "message": "Invalid field 'missing_field' on model 'res.partner'.",
        "model": "res.partner",
        "method": "read",
    }]


@pytest.mark.asyncio
async def test_workspace_call_raw_returns_connector_error_envelope():
    async def fake_tool(tool_name, arguments):
        return {
            "error": True,
            "error_type": "odoo_error",
            "message": "Invalid field 'missing_field' on model 'res.partner'.",
        }

    result = await run_workspace(
        {
            "code": (
                "response = call_raw('odoo', {'model': 'res.partner', 'method': 'read'})\n"
                "print(response['error'], response['error_type'])"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "True odoo_error"
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


def test_workspace_tool_brokers_use_unique_socket_paths(tmp_path):
    first = WorkspaceToolBroker(None, tmp_path / "chat-workspace")
    second = WorkspaceToolBroker(None, tmp_path / "chat-workspace")

    assert first.socket_path
    assert second.socket_path
    assert first.socket_path != second.socket_path


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
async def test_workspace_python_can_call_platform_tool_broker():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"status": "success", "connector": tool_name, "value": arguments["value"]}

    result = await run_workspace(
        {
            "code": (
                "from ai_platform_tools import call\n"
                "print(call('odoo', {'value': 42})['value'])"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "42"
    assert result["connector_calls"] == {"odoo": 1}
    assert calls == [("odoo", {"value": 42})]


@pytest.mark.asyncio
async def test_workspace_python_can_read_connector_guidance_generically():
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"connector": tool_name, "content": "# Skill"}

    result = await run_workspace(
        {
            "code": "payload = call('odoo', {'operation': 'guidance'})\nprint(payload['content'])",
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "# Skill"
    assert calls == [("odoo", {"operation": "guidance"})]


@pytest.mark.asyncio
async def test_workspace_has_standard_file_data_stack_available():
    result = await run_workspace(
        {
            "code": (
                "import numpy\n"
                "import openpyxl\n"
                "import pandas\n"
                "import xlsxwriter\n"
                "print('data-stack-ok')\n"
            ),
            "timeout": 10,
        }
    )

    assert result["status"] == "success"
    assert result["stdout"].strip() == "data-stack-ok"


@pytest.mark.asyncio
async def test_workspace_file_helpers_use_chat_artifact_manifest():
    artifact_id = str(uuid.uuid4())
    calls = []

    async def fake_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        assert tool_name == "document_reader"
        mode = arguments["mode"]
        if mode == "download":
            return {
                "tool_name": "document_reader",
                "mode": "download",
                "artifact_id": artifact_id,
                "filename": "invoice.pdf",
                "content_base64": "aW52b2ljZS1ieXRlcw==",
            }
        if mode == "read":
            return {"tool_name": "document_reader", "mode": "read", "content": "1|Invoice text"}
        if mode == "tables":
            return {"tool_name": "document_reader", "mode": "tables", "tables": [{"table_index": 1, "row_count": 2}]}
        if mode == "layout":
            return {"tool_name": "document_reader", "mode": "layout", "pages": [{"page_number": 1}]}
        raise AssertionError(f"unexpected mode {mode}")

    result = await run_workspace(
        {
            "code": (
                "files = list_files()\n"
                "print(files[0]['filename'])\n"
                "path = download_file('invoice.pdf')\n"
                "print(open(path, 'rb').read().decode())\n"
                "print(read_document('invoice.pdf')['content'])\n"
                "print(read_tables('invoice.pdf')['tables'][0]['row_count'])\n"
                "print(read_layout('invoice.pdf')['pages'][0]['page_number'])\n"
                "save_output('summary.txt', 'done')\n"
            ),
            "timeout": 10,
        },
        tool_executor=fake_tool,
        artifacts=[
            {
                "id": artifact_id,
                "filename": "invoice.pdf",
                "mime_type": "application/pdf",
                "artifact_type": "chat-upload",
                "extraction_status": "pending",
            }
        ],
    )

    assert result["status"] == "success"
    assert result["available_files"][0]["id"] == artifact_id
    assert result["stdout"].splitlines() == [
        "invoice.pdf",
        "invoice-bytes",
        "1|Invoice text",
        "2",
        "1",
    ]
    assert result["connector_calls"] == {"document_reader": 4}
    assert calls == [
        ("document_reader", {"artifact_id": artifact_id, "mode": "download"}),
        ("document_reader", {"artifact_id": artifact_id, "mode": "read"}),
        ("document_reader", {"artifact_id": artifact_id, "mode": "tables"}),
        ("document_reader", {"artifact_id": artifact_id, "mode": "layout"}),
    ]
    assert result["files"][0]["path"] == "outputs/summary.txt"
    assert result["files"][0]["preview"] == "done"


@pytest.mark.asyncio
async def test_workspace_file_helpers_reject_ambiguous_names():
    async def fake_tool(tool_name, arguments):
        return {"tool_name": tool_name, **arguments}

    result = await run_workspace(
        {
            "code": "file_info('invoice')",
            "timeout": 10,
        },
        tool_executor=fake_tool,
        artifacts=[
            {"id": str(uuid.uuid4()), "filename": "invoice-a.pdf", "mime_type": "application/pdf"},
            {"id": str(uuid.uuid4()), "filename": "invoice-b.pdf", "mime_type": "application/pdf"},
        ],
    )

    assert result["status"] == "failed"
    assert "ambiguous" in result["stderr"]


@pytest.mark.asyncio
async def test_workspace_shell_can_call_platform_tool_broker():
    async def fake_tool(tool_name, arguments):
        return {"status": "success", "connector": tool_name, "value": arguments["value"]}

    result = await run_workspace(
        {
            "language": "shell",
            "code": "ai-platform-tool odoo '{\"value\": 42}'",
            "timeout": 10,
        },
        tool_executor=fake_tool,
    )

    assert result["status"] == "success"
    assert '"value": 42' in result["stdout"]
    assert result["connector_calls"] == {"odoo": 1}


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
    assert result["connector_error_details"] == [{
        "tool_name": "odoo",
        "error_type": "connector_error",
        "message": "Connector failed.",
        "model": "res.partner",
        "method": "read",
    }]


@pytest.mark.asyncio
async def test_workspace_runs_shell_and_collects_files():
    result = await run_workspace({
        "language": "terminal",
        "code": "printf 'terminal-ok\\n'\nmkdir -p outputs\nprintf 123 > outputs/terminal.txt",
        "timeout": 10,
    })

    assert result["status"] == "success"
    assert result["language"] == "shell"
    assert result["stdout"].strip() == "terminal-ok"
    assert result["files"] == [
        {
            "path": "outputs/terminal.txt",
            "bytes": 3,
            "mime_type": "text/plain",
            "sha256": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
            "content_base64": "MTIz",
            "preview": "123",
        }
    ]


@pytest.mark.asyncio
async def test_workspace_blocks_local_desktop_launch_commands():
    result = await run_workspace({
        "code": "import subprocess\nresult = subprocess.run(['open', 'outputs/answer.txt'], capture_output=True, text=True)\nprint(result.returncode)\nprint(result.stderr.strip())",
        "timeout": 10,
    })

    assert result["status"] == "success"
    assert "64" in result["stdout"]
    assert "Save deliverables under outputs/" in result["stdout"]


@pytest.mark.asyncio
async def test_workspace_collects_only_explicit_outputs():
    result = await run_workspace({
        "code": (
            "import os\n"
            "open('answer.txt', 'w', encoding='utf-8').write('scratch')\n"
            "os.makedirs('Library/Caches/com.apple.python/x', exist_ok=True)\n"
            "open('Library/Caches/com.apple.python/x/cache.cpython-39.pyc', 'wb').write(b'cache')\n"
            "open(output_path('result.txt'), 'w', encoding='utf-8').write('returned')\n"
        ),
        "timeout": 10,
    })

    assert result["status"] == "success"
    assert result["files"] == [
        {
            "path": "outputs/result.txt",
            "bytes": 8,
            "mime_type": "text/plain",
            "sha256": "68752bf62253f655b49bd5e8d989fc11d439d0e90cccbca77f13180179706cea",
            "content_base64": "cmV0dXJuZWQ=",
            "preview": "returned",
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


# --- Layer-1 capability tests: complex-troubleshooting behaviours of the runtime ---


@pytest.mark.asyncio
async def test_workspace_variables_do_not_persist_between_runs():
    # Regression guard for the statelessness gotcha: a variable defined in one run is
    # NOT available in the next (each run is a fresh process). The guidance tells the
    # model to persist to a file instead; see test below.
    async with WorkspaceSession() as session:
        first = await session.run({"code": "rows = [1, 2, 3, 4]", "timeout": 10})
        second = await session.run({"code": "print(sum(rows))", "timeout": 10})

    assert first["status"] == "success"
    assert second["status"] == "failed"
    assert "NameError" in second["stderr"]


@pytest.mark.asyncio
async def test_workspace_data_persists_across_runs_via_files():
    # The supported way to carry data across runs: write a file in one run, reload it
    # in the next (the session reuses the same working directory).
    async with WorkspaceSession() as session:
        first = await session.run({
            "code": "import json\nopen('scratch.json', 'w').write(json.dumps([1, 2, 3, 4]))",
            "timeout": 10,
        })
        second = await session.run({
            "code": "import json\nprint(sum(json.load(open('scratch.json'))))",
            "timeout": 10,
        })

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert second["stdout"].strip() == "10"


@pytest.mark.asyncio
async def test_workspace_handles_large_payload_in_one_script():
    # A realistic complex task: build a large dataset, compute a gap analysis over it,
    # and write a summary — all in a single script, within the runtime limits.
    code = (
        "rows = [{'id': i, 'code': str(i)} for i in range(50000)]\n"
        "present = {r['id'] for r in rows}\n"
        "missing = [i for i in range(50000) if i not in present]\n"
        "print(len(rows), len(missing))"
    )
    result = await run_workspace({"code": code, "timeout": 25})

    assert result["status"] == "success"
    assert result["stdout"].strip() == "50000 0"


@pytest.mark.asyncio
async def test_workspace_surfaces_runtime_exception_cleanly():
    # An exception must come back as a structured failure with the error visible in
    # stderr — never a hang or a swallowed error.
    result = await run_workspace({"code": "raise ValueError('boom')", "timeout": 10})

    assert result["status"] == "failed"
    assert result["timed_out"] is False
    assert result["exit_code"] not in (0, None)
    assert "ValueError" in result["stderr"]
    assert "boom" in result["stderr"]


@pytest.mark.asyncio
async def test_workspace_enforces_timeout():
    # A runaway script is stopped at the timeout and reported as timed out, not left
    # to spin.
    result = await run_workspace({"code": "while True:\n    pass", "timeout": 2})

    assert result["status"] == "failed"
    assert result["timed_out"] is True
