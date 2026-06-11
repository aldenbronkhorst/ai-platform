"""Add shared Microsoft device auth sessions.

Revision ID: 013_ms_device_auth
Revises: 012_split_ms_native
Create Date: 2026-06-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "013_ms_device_auth"
down_revision = "012_split_ms_native"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_microsoft_device_auth_sessions",
        sa.Column("auth_session_id", sa.String(64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_users.id"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("device_code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("poll_interval", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_in_flight_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_ms_device_auth_user"),
    )
    op.create_index(
        "ix_ms_device_auth_user",
        "ai_microsoft_device_auth_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_ms_device_auth_expires_at",
        "ai_microsoft_device_auth_sessions",
        ["expires_at"],
    )


def downgrade():
    op.drop_index("ix_ms_device_auth_expires_at", table_name="ai_microsoft_device_auth_sessions")
    op.drop_index("ix_ms_device_auth_user", table_name="ai_microsoft_device_auth_sessions")
    op.drop_table("ai_microsoft_device_auth_sessions")
