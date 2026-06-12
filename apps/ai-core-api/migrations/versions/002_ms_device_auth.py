"""Add Microsoft device auth sessions table.

Revision ID: 002_ms_device_auth
Revises: 001_initial
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "002_ms_device_auth"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_microsoft_device_auth_sessions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(TABLE_NAME):
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auth_session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("device_code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("poll_interval", sa.Integer(), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_in_flight_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["ai_users.id"]),
        sa.PrimaryKeyConstraint("auth_session_id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_ms_device_auth_user_provider"),
    )
    op.create_index(
        "ix_ai_microsoft_device_auth_sessions_user_id",
        TABLE_NAME,
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return

    op.drop_index("ix_ai_microsoft_device_auth_sessions_user_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
