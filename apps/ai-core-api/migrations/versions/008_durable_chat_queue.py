"""Add durable chat-turn queue payloads and worker leases.

Revision ID: 008_durable_chat_queue
Revises: 007_generic_connector_accounts
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "008_durable_chat_queue"
down_revision: Union[str, None] = "007_generic_connector_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_chat_turns", sa.Column("request_payload_json", sa.JSON(), nullable=True))
    op.add_column("ai_chat_turns", sa.Column("user_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("ai_chat_turns", sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("ai_chat_turns", sa.Column("lease_owner", sa.String(length=255), nullable=True))
    op.add_column("ai_chat_turns", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_chat_turns", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key(
        "fk_ai_chat_turns_user_message",
        "ai_chat_turns",
        "ai_chat_messages",
        ["user_message_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_ai_chat_turns_assistant_message",
        "ai_chat_turns",
        "ai_chat_messages",
        ["assistant_message_id"],
        ["id"],
    )
    op.create_index("ix_ai_chat_turns_lease_owner", "ai_chat_turns", ["lease_owner"])
    op.create_index("ix_ai_chat_turns_lease_expires_at", "ai_chat_turns", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_chat_turns_lease_expires_at", table_name="ai_chat_turns")
    op.drop_index("ix_ai_chat_turns_lease_owner", table_name="ai_chat_turns")
    op.drop_constraint("fk_ai_chat_turns_assistant_message", "ai_chat_turns", type_="foreignkey")
    op.drop_constraint("fk_ai_chat_turns_user_message", "ai_chat_turns", type_="foreignkey")
    op.drop_column("ai_chat_turns", "attempt_count")
    op.drop_column("ai_chat_turns", "lease_expires_at")
    op.drop_column("ai_chat_turns", "lease_owner")
    op.drop_column("ai_chat_turns", "assistant_message_id")
    op.drop_column("ai_chat_turns", "user_message_id")
    op.drop_column("ai_chat_turns", "request_payload_json")
