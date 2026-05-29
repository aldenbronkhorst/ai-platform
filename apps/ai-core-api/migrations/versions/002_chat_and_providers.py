"""add chat tables + model provider tables

Revision ID: 002_chat_and_providers
Revises: 001_initial
Create Date: 2026-05-29

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid

revision: str = "002_chat_and_providers"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("ai_users.id"), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("workflow_context", sa.String(100), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("chat_session_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_sessions.id"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("ai_users.id"), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("model_provider", sa.String(100), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("token_usage_json", sa.JSON, nullable=True),
        sa.Column("tool_call_json", sa.JSON, nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_chat_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("chat_session_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_sessions.id"), nullable=False, index=True),
        sa.Column("artifact_id", UUID(as_uuid=True), sa.ForeignKey("ai_artifacts.id"), nullable=False, index=True),
        sa.Column("linked_message_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_messages.id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_chat_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("chat_session_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_sessions.id"), nullable=False, index=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("ai_jobs.id"), nullable=False, index=True),
        sa.Column("linked_message_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_messages.id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("auth_type", sa.String(30), nullable=False, server_default="key_vault_secret"),
        sa.Column("secret_reference", sa.String(500), nullable=True),
        sa.Column("enabled", sa.String(10), nullable=False, server_default="true"),
        sa.Column("capabilities", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("provider_id", UUID(as_uuid=True), sa.ForeignKey("ai_providers.id"), nullable=False, index=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("deployment_name", sa.String(255), nullable=False),
        sa.Column("model_family", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("supports_tools", sa.String(10), nullable=False, server_default="false"),
        sa.Column("supports_json_schema", sa.String(10), nullable=False, server_default="false"),
        sa.Column("context_window", sa.Integer, nullable=True),
        sa.Column("enabled", sa.String(10), nullable=False, server_default="true"),
        sa.Column("config_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_routes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("task_type", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("primary_model_id", UUID(as_uuid=True), sa.ForeignKey("ai_models.id"), nullable=False),
        sa.Column("fallback_model_id", UUID(as_uuid=True), sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("temperature", sa.Numeric(4, 2), nullable=False, server_default="0.3"),
        sa.Column("max_tokens", sa.Integer, nullable=False, server_default="2000"),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column("enabled", sa.String(10), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_usage_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("provider_id", UUID(as_uuid=True), sa.ForeignKey("ai_providers.id"), nullable=True),
        sa.Column("model_id", UUID(as_uuid=True), sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("route_id", UUID(as_uuid=True), sa.ForeignKey("ai_routes.id"), nullable=True),
        sa.Column("task_type", sa.String(100), nullable=True),
        sa.Column("chat_session_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_sessions.id"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("ai_users.id"), nullable=True, index=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("cost_estimate", sa.Numeric(12, 6), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ai_usage_logs")
    op.drop_table("ai_routes")
    op.drop_table("ai_models")
    op.drop_table("ai_providers")
    op.drop_table("ai_chat_jobs")
    op.drop_table("ai_chat_artifacts")
    op.drop_table("ai_chat_messages")
    op.drop_table("ai_chat_sessions")
