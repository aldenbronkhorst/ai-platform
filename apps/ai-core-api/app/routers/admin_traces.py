"""Admin trace query endpoints for observability."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import require_role
from app.models.models import AITrace, AITraceSpan, AIUsageLog

router = APIRouter(prefix="/admin/traces", tags=["Admin"])
logger = logging.getLogger(__name__)


@router.get("")
async def list_traces(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin"])),
    request_id: Optional[str] = Query(None, description="Filter by request ID"),
    trace_id: Optional[str] = Query(None, description="Filter by trace ID"),
    operation_type: Optional[str] = Query(None, description="Filter by operation type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    model: Optional[str] = Query(None, description="Filter by model name"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    connector: Optional[str] = Query(None, description="Filter by connector"),
    error_type: Optional[str] = Query(None, description="Filter by error type"),
    min_duration_ms: Optional[int] = Query(None, description="Min duration in ms"),
    max_duration_ms: Optional[int] = Query(None, description="Max duration in ms"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(50, description="Max results", le=200),
    offset: int = Query(0, description="Offset"),
):
    """List traces with optional filters."""
    query = select(AITrace).order_by(desc(AITrace.created_at))

    if request_id:
        query = query.where(AITrace.request_id == request_id)
    if trace_id:
        query = query.where(AITrace.trace_id == trace_id)
    if operation_type:
        query = query.where(AITrace.operation_type == operation_type)
    if status:
        query = query.where(AITrace.status == status)
    if user_id:
        query = query.where(AITrace.user_id == UUID(user_id))
    if model:
        query = query.where(AITrace.model == model)
    if provider:
        query = query.where(AITrace.provider == provider)
    if connector:
        query = query.where(AITrace.connector == connector)
    if error_type:
        query = query.where(AITrace.error_type == error_type)
    if min_duration_ms is not None:
        query = query.where(AITrace.duration_ms >= min_duration_ms)
    if max_duration_ms is not None:
        query = query.where(AITrace.duration_ms <= max_duration_ms)
    if date_from:
        query = query.where(AITrace.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.where(AITrace.created_at <= datetime.fromisoformat(date_to))

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    traces = result.scalars().all()

    return {
        "total": len(traces),
        "traces": [
            {
                "trace_id": t.trace_id,
                "request_id": t.request_id,
                "operation_type": t.operation_type,
                "operation_name": t.operation_name,
                "status": t.status,
                "user_id": str(t.user_id) if t.user_id else None,
                "chat_session_id": str(t.chat_session_id) if t.chat_session_id else None,
                "provider": t.provider,
                "model": t.model,
                "connector": t.connector,
                "duration_ms": t.duration_ms,
                "error_type": t.error_type,
                "error_message": t.error_message,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in traces
        ],
    }


@router.get("/{trace_id}")
async def get_trace(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin"])),
):
    """Get a single trace with all its spans."""
    result = await db.execute(select(AITrace).where(AITrace.trace_id == trace_id))
    trace = result.scalar_one_or_none()
    if not trace:
        raise HTTPException(status_code=404, detail={"error": "trace_not_found"})

    span_result = await db.execute(
        select(AITraceSpan).where(AITraceSpan.trace_id == trace_id)
        .order_by(AITraceSpan.started_at.asc())
    )
    spans = span_result.scalars().all()
    usage_result = await db.execute(
        select(AIUsageLog).where(
            (AIUsageLog.trace_id == trace.trace_id) | (AIUsageLog.request_id == trace.request_id)
        ).order_by(AIUsageLog.timestamp.asc())
    )
    usage_logs = usage_result.scalars().all()

    return {
        "trace": {
            "trace_id": trace.trace_id,
            "request_id": trace.request_id,
            "operation_type": trace.operation_type,
            "operation_name": trace.operation_name,
            "status": trace.status,
            "user_id": str(trace.user_id) if trace.user_id else None,
            "chat_session_id": str(trace.chat_session_id) if trace.chat_session_id else None,
            "message_id": str(trace.message_id) if trace.message_id else None,
            "provider": trace.provider,
            "model": trace.model,
            "connector": trace.connector,
            "route_id": str(trace.route_id) if trace.route_id else None,
            "started_at": trace.started_at.isoformat() if trace.started_at else None,
            "ended_at": trace.ended_at.isoformat() if trace.ended_at else None,
            "duration_ms": trace.duration_ms,
            "error_type": trace.error_type,
            "error_message": trace.error_message,
            "metadata": trace.metadata_json,
            "created_at": trace.created_at.isoformat() if trace.created_at else None,
        },
        "spans": [
            {
                "span_id": s.span_id,
                "parent_span_id": s.parent_span_id,
                "span_type": s.span_type,
                "span_name": s.span_name,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_ms": s.duration_ms,
                "input_summary": s.input_summary_json,
                "output_summary": s.output_summary_json,
                "error_type": s.error_type,
                "error_message": s.error_message,
                "metadata": s.metadata_json,
            }
            for s in spans
        ],
        "usage_logs": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "request_id": log.request_id,
                "trace_id": log.trace_id,
                "provider_id": str(log.provider_id) if log.provider_id else None,
                "model_id": str(log.model_id) if log.model_id else None,
                "route_id": str(log.route_id) if log.route_id else None,
                "task_type": log.task_type,
                "chat_session_id": str(log.chat_session_id) if log.chat_session_id else None,
                "user_id": str(log.user_id) if log.user_id else None,
                "prompt_tokens": log.prompt_tokens,
                "completion_tokens": log.completion_tokens,
                "total_tokens": log.total_tokens,
                "latency_ms": log.latency_ms,
                "status": log.status,
                "error_message": log.error_message,
            }
            for log in usage_logs
        ],
    }
