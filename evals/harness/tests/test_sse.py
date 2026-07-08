from sse import final_message, parse_sse

STREAM = """event: started
data: {"request_id": "r1"}

: keep-alive comment

event: tool.start
data: {"id": "t1", "name": "workspace"}

event: message.complete
data: {"role": "assistant", "content": "done", "tool_call_json": [{"tool_name": "workspace"}]}

event: done
data: {"request_id": "r1"}
"""


def test_parse_sse_frames():
    events = list(parse_sse(STREAM.splitlines()))
    types = [ev for ev, _ in events]
    assert types == ["started", "tool.start", "message.complete", "done"]
    assert events[0][1]["request_id"] == "r1"


def test_final_message_picks_assistant_complete():
    events = list(parse_sse(STREAM.splitlines()))
    msg = final_message(events)
    assert msg["role"] == "assistant"
    assert msg["content"] == "done"
    assert msg["tool_call_json"][0]["tool_name"] == "workspace"


def test_malformed_data_becomes_raw():
    events = list(parse_sse(["event: x", "data: {not json", ""]))
    assert events == [("x", {"_raw": "{not json"})]


def test_multiline_data_is_joined():
    events = list(parse_sse(["event: m", 'data: {"a":', 'data: 1}', ""]))
    assert events == [("m", {"a": 1})]
