"""Add AITrace and AITraceSpan tables

Revision ID: 006
Revises: 005_add_memory_usage
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = "006"
down_revision = "005_add_memory_usage"


def upgrade():
    op.create_table(
        "ai_traces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("request_id", sa.String(100), nullable=False, index=True),
        sa.Column("operation_type", sa.String(50), nullable=False, index=True),
        sa.Column("operation_name", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("ai_users.id"), nullable=True, index=True),
        sa.Column("chat_session_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_sessions.id"), nullable=True, index=True),
        sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_messages.id"), nullable=True),
        sa.Column("connector", sa.String(50), nullable=True),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("route_id", UUID(as_uuid=True), sa.ForeignKey("ai_routes.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "ai_trace_spans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", sa.String(100), sa.ForeignKey("ai_traces.trace_id"), nullable=False, index=True),
        sa.Column("span_id", sa.String(100), nullable=False),
        sa.Column("parent_span_id", sa.String(100), nullable=True),
        sa.Column("span_type", sa.String(50), nullable=False),
        sa.Column("span_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_summary_json", JSON(), nullable=True),
        sa.Column("output_summary_json", JSON(), nullable=True),
        sa.Column("error_type", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_traces_user_id_created", "ai_traces", ["user_id", "created_at"])
    op.create_index("ix_traces_operation_type_status", "ai_traces", ["operation_type", "status"])
    op.create_index("ix_traces_duration_ms", "ai_traces", ["duration_ms"])
    op.create_index("ix_trace_spans_trace_id_type", "ai_trace_spans", ["trace_id", "span_type"])


def downgrade():
    op.drop_table("ai_trace_spans")
    op.drop_table("ai_traces")
