"""add ai_memory_usage_events table

Revision ID: 005_add_memory_usage
Revises: 004_add_memories_and_currency
Create Date: 2026-05-31

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005_add_memory_usage"
down_revision: Union[str, None] = "004_add_memories_and_currency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    if 'ai_memory_usage_events' not in tables:
        op.create_table(
            "ai_memory_usage_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("memory_id", UUID(as_uuid=True), sa.ForeignKey("ai_memories.id"), nullable=False, index=True),
            sa.Column("chat_session_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_sessions.id"), nullable=True, index=True),
            sa.Column("chat_message_id", UUID(as_uuid=True), sa.ForeignKey("ai_chat_messages.id"), nullable=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("ai_users.id"), nullable=True, index=True),
            sa.Column("request_id", sa.String(100), nullable=True),
            sa.Column("used_in_context", sa.String(10), server_default="true", nullable=False),
            sa.Column("used_in_final_answer", sa.String(10), server_default="true", nullable=False),
            sa.Column("feedback_type", sa.String(50), nullable=True),
            sa.Column("feedback_value", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("ai_memory_usage_events")
