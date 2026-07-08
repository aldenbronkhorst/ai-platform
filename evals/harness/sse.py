"""Server-Sent-Events parsing + live capture against the chat stream.

The SSE framing (`event: <type>\\ndata: <json>\\n\\n`) parser is pure and unit-
tested offline. The live `create_session` / `capture_turn` functions use httpx
and only run against a reachable staging deployment; they are thin and not unit
covered (no creds in CI).

Verified contract (ai-core-api chat.py):
  POST /chat/sessions                              -> {"id": <session_id>, ...}
  POST /chat/sessions/{id}/messages/stream         body {"content": str}
  auth header: X-API-Key: <staging api-key secret>
  the authoritative final answer + full tool calls arrive on `message.complete`:
    {role, content, tool_call_json, token_usage_json, metadata_json, ...}
"""
from __future__ import annotations

import json
from typing import Iterable, Iterator


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """Yield (event_type, data) for each `event:/data:` frame in a line stream."""
    event: str | None = None
    data_lines: list[str] = []

    def flush() -> Iterator[tuple[str, dict]]:
        nonlocal event, data_lines
        if event is not None:
            payload = "\n".join(data_lines)
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {"_raw": payload}
            yield event, data
        event, data_lines = None, []

    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            yield from flush()
            continue
        if line.startswith(":"):  # SSE comment / keep-alive
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    yield from flush()


def final_message(events: Iterable[tuple[str, dict]]) -> dict:
    """The authoritative assistant turn = last `message.complete` (role=assistant)."""
    completes = [data for ev, data in events if ev == "message.complete"]
    assistant = [c for c in completes if c.get("role") == "assistant"]
    chosen = assistant or completes
    return chosen[-1] if chosen else {}


# ── live capture (needs staging) ──────────────────────────────────────────────

def create_session(base_url: str, api_key: str, *, title: str = "eval", timeout: float = 30.0) -> str:
    import httpx

    r = httpx.post(
        f"{base_url.rstrip('/')}/chat/sessions",
        headers={"X-API-Key": api_key},
        json={"title": title},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["id"]


def capture_turn(base_url: str, api_key: str, session_id: str, content: str, *, timeout: float = 240.0):
    """Send one user message, consume the SSE stream, return a CapturedTurn."""
    import httpx

    from score import CapturedTurn  # local import to avoid a hard dep at import time

    url = f"{base_url.rstrip('/')}/chat/sessions/{session_id}/messages/stream"
    events: list[tuple[str, dict]] = []
    with httpx.stream(
        "POST", url,
        headers={"X-API-Key": api_key},
        json={"content": content},
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for ev, data in parse_sse(resp.iter_lines()):
            if ev == "error":
                raise RuntimeError(f"stream error: {data}")
            events.append((ev, data))

    payload = final_message(events)
    return CapturedTurn(
        content=payload.get("content") or "",
        tool_call_json=payload.get("tool_call_json") or [],
        token_usage=payload.get("token_usage_json"),
    )
