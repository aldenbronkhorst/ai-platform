from extract import (
    extract_odoo_calls_from_code,
    extract_odoo_calls_from_tool_calls,
    is_write_method,
)


def test_playbook_literal():
    calls = extract_odoo_calls_from_code(
        "pb = call('odoo', {'operation': 'playbook', 'name': 'records-missing'})"
    )
    assert len(calls) == 1
    assert calls[0].operation == "playbook"
    assert calls[0].name == "records-missing"
    assert calls[0].dynamic is False


def test_single_write_and_read_classification():
    write = extract_odoo_calls_from_code(
        "call('odoo', {'model': 'sale.order', 'method': 'write', 'args': [[1], {'x': 2}]})"
    )[0]
    assert write.method == "write"
    assert write.write_methods() == ["write"]

    read = extract_odoo_calls_from_code(
        "call('odoo', {'model': 'sale.order', 'method': 'search_read', 'args': [[]]})"
    )[0]
    assert read.method == "search_read"
    assert read.write_methods() == []


def test_batch_calls():
    code = "call('odoo', {'calls': [{'model':'a','method':'create'}, {'model':'b','method':'read'}]})"
    call = extract_odoo_calls_from_code(code)[0]
    assert call.is_batch is True
    assert call.sub_methods == ["create", "read"]
    assert call.write_methods() == ["create"]


def test_default_deny_catches_workflow_writes():
    # not create/write/unlink, but still a mutation -> must be a write
    assert is_write_method("action_post") is True
    assert is_write_method("button_draft") is True
    assert is_write_method("search_read") is False


def test_dynamic_name_is_flagged():
    call = extract_odoo_calls_from_code(
        "call('odoo', {'operation': 'playbook', 'name': chosen_playbook})"
    )[0]
    assert call.operation == "playbook"
    assert call.name is None
    assert call.dynamic is True


def test_syntax_error_falls_back_to_regex():
    # deliberately unparseable Python, but a literal odoo write is visible
    code = "def (:\n    call('odoo', {'model': 'account.move', 'method': 'unlink'})"
    calls = extract_odoo_calls_from_code(code)
    assert len(calls) == 1
    assert calls[0].dynamic is True
    assert calls[0].method == "unlink"
    assert calls[0].write_methods() == ["unlink"]


def test_from_tool_calls_only_reads_workspace():
    tool_calls = [
        {"tool_name": "workspace", "arguments": {"code": "call('odoo', {'method': 'create', 'model': 'x'})"}},
        {"tool_name": "ms_graph", "arguments": {"path": "/users"}},  # ignored
    ]
    calls = extract_odoo_calls_from_tool_calls(tool_calls)
    assert len(calls) == 1
    assert calls[0].method == "create"


def test_arguments_as_json_string():
    tool_calls = [
        {"tool_name": "workspace", "arguments": "{\"code\": \"call('odoo', {'operation':'playbook','name':'write-failed'})\"}"}
    ]
    calls = extract_odoo_calls_from_tool_calls(tool_calls)
    assert calls and calls[0].name == "write-failed"
