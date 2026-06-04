"""Platform-wide observability and tracing service.

Every operation in AI Platform gets a trace with spans for each step.
Traces are persisted to the database and include redacted payload summaries.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import AITrace, AITraceSpan

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "api_key", "password", "secret", "token", "authorization", "cookie",
    "credentials", "datas", "content_base64",
}

NON_SECRET_TOKEN_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "max_tokens",
    "token_count",
    "tokens_used",
}


def redact_value(key: str, value: Any, depth: int = 0) -> Any:
    """Redact sensitive values recursively. Returns a safe-for-storage copy."""
    if depth > 10:
        return {"__truncated__": True}
    key_lower = key.lower()
    is_sensitive = (
        key_lower in SENSITIVE_KEYS
        or any(s in key_lower for s in ["_key", "_secret", "_password", "_token"])
    )
    if key_lower in NON_SECRET_TOKEN_KEYS:
        is_sensitive = False
    if is_sensitive:
        if isinstance(value, str) and value:
            h = hashlib.sha256(value.encode()).hexdigest()
            return {"present": True, "fingerprint": f"sha256:{h[:8]}...{h[-4:]}", "type": type(value).__name__}
        return {"present": bool(value), "type": type(value).__name__}
    if isinstance(value, dict):
        return {k: redact_value(k, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 20:
            return [redact_value(key, v, depth + 1) for v in value[:5]] + [f"... ({len(value) - 5} more)"]
        return [redact_value(key, v, depth + 1) for v in value]
    if isinstance(value, str) and len(value) > 10000:
        return {"truncated": True, "length": len(value), "preview": value[:200]}
    return value


def make_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:20]}"


def make_request_id() -> str:
    return uuid.uuid4().hex[:20]


def make_span_id() -> str:
    return f"span_{uuid.uuid4().hex[:12]}"


def summarize_payload(data: Any, max_keys: int = 10) -> dict:
    """Create a compact summary of a payload for trace storage."""
    if isinstance(data, dict):
        return {k: _summarize_value(v) for k in list(data.keys())[:max_keys]}
    if isinstance(data, list):
        return {"count": len(data), "sample": [_summarize_value(v) for v in data[:3]]}
    return _summarize_value(data)


def _summarize_value(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _summarize_value(v[k]) for k in list(v.keys())[:5]}
    if isinstance(v, list):
        return [len(v), _summarize_value(v[0]) if v else None]
    if isinstance(v, str) and len(v) > 200:
        return v[:200] + "..."
    return v


def estimate_prompt_tokens(text: str) -> int:
    """Rough token estimation (~4 chars per token)."""
    return len(text) // 4


def _activity_input_summary(span_type: str, data: dict[str, Any]) -> dict[str, Any]:
    if span_type == "provider_call":
        request = data.get("request") if isinstance(data.get("request"), dict) else {}
        model = data.get("model") if isinstance(data.get("model"), dict) else {}
        return {
            "attempt_reason": data.get("attempt_reason"),
            "provider": data.get("provider"),
            "provider_type": data.get("provider_type"),
            "model": {
                "display_name": model.get("display_name"),
                "model_name": model.get("model_name"),
                "supports_tools": model.get("supports_tools"),
                "context_window": model.get("context_window"),
            },
            "message_count": data.get("message_count"),
            "tool_count": data.get("tool_count"),
            "request": {
                "temperature": request.get("temperature"),
                "max_tokens": request.get("max_tokens"),
            },
        }
    if span_type == "tool_call":
        args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        return {
            "tool_name": data.get("tool_name"),
            "arguments": {
                key: value
                for key, value in args.items()
                if key in {"command", "query", "model", "operation", "resource", "timeout"}
            } or {"argument_keys": sorted(args.keys())[:8]},
        }
    if span_type == "context_build":
        return {
            "task_type": data.get("task_type"),
            "request_id": data.get("request_id"),
            "message_count": data.get("message_count"),
        }
    return summarize_payload(data)


def _activity_output_summary(span_type: str, data: dict[str, Any]) -> dict[str, Any]:
    if span_type == "tool_call":
        result = data.get("result")
        if isinstance(result, dict):
            return {
                "result": {
                    "status": result.get("status"),
                    "error": bool(result.get("error")),
                    "error_type": result.get("error_type"),
                    "message": result.get("message") or result.get("error"),
                    "count": result.get("count") if isinstance(result.get("count"), int) else None,
                    "keys": sorted(result.keys())[:10],
                }
            }
        if isinstance(result, list):
            return {"result": {"count": len(result), "type": "list"}}
        return {"result": {"type": type(result).__name__}}
    if span_type in {"provider_call", "context_build", "model_request"}:
        return summarize_payload(data)
    return summarize_payload(data)


def activity_safe_event(event: dict[str, Any]) -> dict[str, Any]:
    span_type = str(event.get("span_type") or "")
    safe = {key: value for key, value in event.items() if key not in {"input_summary", "output_summary"}}
    input_summary = event.get("input_summary")
    output_summary = event.get("output_summary")
    if isinstance(input_summary, dict):
        safe["input_summary"] = _activity_input_summary(span_type, input_summary)
    if isinstance(output_summary, dict):
        safe["output_summary"] = _activity_output_summary(span_type, output_summary)
    return safe


class TraceService:
    """Manages trace creation, span tracking, and persistence."""

    def __init__(
        self,
        db: AsyncSession,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        activity_event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.db = db
        self.trace_id = trace_id or make_trace_id()
        self.request_id = request_id or make_request_id()
        self._trace: Optional[AITrace] = None
        self._spans: dict[str, AITraceSpan] = {}
        self._span_stack: list[str] = []
        self._activity_event_sink = activity_event_sink

    def _emit_activity(self, event: dict[str, Any]) -> None:
        if not self._activity_event_sink:
            return
        try:
            self._activity_event_sink(redact_value("activity", activity_safe_event(event)))
        except Exception as exc:
            logger.warning("Failed to emit trace activity event: %s", exc)

    def begin(self, operation_type: str, operation_name: str = "", user_id: Any = None,
              chat_session_id: Any = None, message_id: Any = None, connector: str = None,
              provider: str = None, model: str = None, route_id: Any = None,
              metadata: dict = None) -> str:
        now = datetime.now(timezone.utc)
        self._trace = AITrace(
            id=uuid.uuid4(),
            trace_id=self.trace_id,
            request_id=self.request_id,
            operation_type=operation_type,
            operation_name=operation_name,
            status="running",
            user_id=user_id,
            chat_session_id=chat_session_id,
            message_id=message_id,
            connector=connector,
            provider=provider,
            model=model,
            route_id=route_id,
            started_at=now,
            metadata_json=metadata or {},
        )
        return self.trace_id

    async def commit(self, status: str = "success", error_type: str = None, error_message: str = None):
        if not self._trace:
            return
        now = datetime.now(timezone.utc)
        self._trace.ended_at = now
        if self._trace.started_at:
            self._trace.duration_ms = int((now - self._trace.started_at).total_seconds() * 1000)
        self._trace.status = status
        self._trace.error_type = error_type
        self._trace.error_message = error_message
        self.db.add(self._trace)
        for span in self._spans.values():
            self.db.add(span)
        try:
            if hasattr(self.db, 'flush') and callable(self.db.flush):
                await self.db.flush()
        except Exception as e:
            logger.warning("Failed to flush trace: %s", e)

    def start_span(self, span_type: str, span_name: str, parent_span_id: str = None,
                   input_summary: dict = None, metadata: dict = None) -> str:
        span_id = make_span_id()
        now = datetime.now(timezone.utc)
        parent_id = parent_span_id or (self._span_stack[-1] if self._span_stack else None)
        span = AITraceSpan(
            id=uuid.uuid4(),
            trace_id=self.trace_id,
            span_id=span_id,
            parent_span_id=parent_id,
            span_type=span_type,
            span_name=span_name,
            status="running",
            started_at=now,
            metadata_json=metadata or {},
        )
        if input_summary:
            span.input_summary_json = redact_value("input", input_summary)
        self._spans[span_id] = span
        self._span_stack.append(span_id)
        self._emit_activity({
            "event": "span_started",
            "span_id": span_id,
            "parent_span_id": parent_id,
            "span_type": span_type,
            "span_name": span_name,
            "status": "running",
            "started_at": now.isoformat(),
            "input_summary": input_summary or {},
            "metadata": metadata or {},
        })
        return span_id

    def end_span(self, span_id: str = None, status: str = "success", output_summary: dict = None,
                 error_type: str = None, error_message: str = None):
        if not span_id:
            if self._span_stack:
                span_id = self._span_stack.pop()
            else:
                return
        span = self._spans.get(span_id)
        if not span:
            return
        if span_id in self._span_stack:
            self._span_stack.remove(span_id)
        now = datetime.now(timezone.utc)
        span.ended_at = now
        if span.started_at:
            span.duration_ms = int((now - span.started_at).total_seconds() * 1000)
        span.status = status
        span.error_type = error_type
        span.error_message = error_message
        if output_summary:
            span.output_summary_json = redact_value("output", output_summary)
        self._emit_activity({
            "event": "span_finished",
            "span_id": span_id,
            "parent_span_id": span.parent_span_id,
            "span_type": span.span_type,
            "span_name": span.span_name,
            "status": status,
            "started_at": span.started_at.isoformat() if span.started_at else None,
            "ended_at": now.isoformat(),
            "duration_ms": span.duration_ms,
            "output_summary": output_summary or {},
            "error_type": error_type,
            "error_message": error_message,
            "metadata": span.metadata_json or {},
        })

    def span_error(self, span_id: str, error_type: str, error_message: str):
        self.end_span(span_id, status="failed", error_type=error_type, error_message=error_message)

    def add_metadata(self, span_id: str = None, metadata: dict = None):
        target = None
        if span_id:
            target = self._spans.get(span_id)
        else:
            target = self._trace
        if target and metadata:
            existing = target.metadata_json or {}
            existing.update(metadata)
            target.metadata_json = existing
