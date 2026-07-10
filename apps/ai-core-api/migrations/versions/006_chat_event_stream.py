"""Add durable chat events and server-owned turns.

Revision ID: 006_chat_event_stream
Revises: 005_drop_route_fallback_model
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "006_chat_event_stream"
down_revision: Union[str, None] = "005_drop_route_fallback_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_turns",
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("chat_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_session_id"], ["ai_chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["ai_users.id"]),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index("ix_ai_chat_turns_chat_session_id", "ai_chat_turns", ["chat_session_id"])
    op.create_index("ix_ai_chat_turns_user_id", "ai_chat_turns", ["user_id"])
    op.create_index("ix_ai_chat_turns_status", "ai_chat_turns", ["status"])
    op.create_index("ix_ai_chat_turns_updated_at", "ai_chat_turns", ["updated_at"])
    op.create_index(
        "uq_ai_chat_turns_active_session",
        "ai_chat_turns",
        ["chat_session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "ai_chat_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_session_id"], ["ai_chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["ai_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_chat_events_chat_session_id", "ai_chat_events", ["chat_session_id"])
    op.create_index("ix_ai_chat_events_user_id", "ai_chat_events", ["user_id"])
    op.create_index("ix_ai_chat_events_request_id", "ai_chat_events", ["request_id"])
    op.create_index("ix_ai_chat_events_created_at", "ai_chat_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_chat_events_created_at", table_name="ai_chat_events")
    op.drop_index("ix_ai_chat_events_request_id", table_name="ai_chat_events")
    op.drop_index("ix_ai_chat_events_user_id", table_name="ai_chat_events")
    op.drop_index("ix_ai_chat_events_chat_session_id", table_name="ai_chat_events")
    op.drop_table("ai_chat_events")
    op.drop_index("uq_ai_chat_turns_active_session", table_name="ai_chat_turns")
    op.drop_index("ix_ai_chat_turns_updated_at", table_name="ai_chat_turns")
    op.drop_index("ix_ai_chat_turns_status", table_name="ai_chat_turns")
    op.drop_index("ix_ai_chat_turns_user_id", table_name="ai_chat_turns")
    op.drop_index("ix_ai_chat_turns_chat_session_id", table_name="ai_chat_turns")
    op.drop_table("ai_chat_turns")
