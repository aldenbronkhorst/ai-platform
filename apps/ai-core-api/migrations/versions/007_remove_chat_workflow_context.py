"""Remove legacy chat workflow context

Revision ID: 007
Revises: 006
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade():
    if _has_column("ai_chat_sessions", "workflow_context"):
        op.drop_column("ai_chat_sessions", "workflow_context")


def downgrade():
    if not _has_column("ai_chat_sessions", "workflow_context"):
        op.add_column("ai_chat_sessions", sa.Column("workflow_context", sa.String(100), nullable=True))
