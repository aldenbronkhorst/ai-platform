"""add ai_memories table and currency fields to ai_connected_accounts

Revision ID: 004_add_memories_and_currency
Revises: 003_add_odoo_url_db
Create Date: 2026-05-30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision: str = "004_add_memories_and_currency"
down_revision: Union[str, None] = "003_add_odoo_url_db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    if 'ai_memories' not in tables:
        op.create_table(
            "ai_memories",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("type", sa.String(50), nullable=False, index=True),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("summary", sa.String(1000), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("scope_type", sa.String(50), nullable=True),
            sa.Column("scope_value", sa.String(255), nullable=True),
            sa.Column("entities_json", JSON(), nullable=True),
            sa.Column("source_type", sa.String(50), nullable=True),
            sa.Column("source_id", sa.String(255), nullable=True),
            sa.Column("conversation_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("message_id", UUID(as_uuid=True), nullable=True),
            sa.Column("confidence", sa.String(20), nullable=False, server_default="medium"),
            sa.Column("risk_level", sa.String(20), nullable=False, server_default="low"),
            sa.Column("status", sa.String(20), nullable=False, index=True, server_default="draft"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("stale_after", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True),
            sa.Column("approved_by_user_id", UUID(as_uuid=True), nullable=True),
            sa.Column("supersedes_memory_id", UUID(as_uuid=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("metadata_json", JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    cols = {c['name'] for c in inspector.get_columns('ai_connected_accounts')}
    if 'odoo_company_id' not in cols:
        op.add_column("ai_connected_accounts", sa.Column("odoo_company_id", sa.Integer(), nullable=True))
    if 'odoo_company_name' not in cols:
        op.add_column("ai_connected_accounts", sa.Column("odoo_company_name", sa.String(255), nullable=True))
    if 'odoo_currency_code' not in cols:
        op.add_column("ai_connected_accounts", sa.Column("odoo_currency_code", sa.String(10), nullable=True))
    if 'odoo_currency_symbol' not in cols:
        op.add_column("ai_connected_accounts", sa.Column("odoo_currency_symbol", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_connected_accounts", "odoo_currency_symbol")
    op.drop_column("ai_connected_accounts", "odoo_currency_code")
    op.drop_column("ai_connected_accounts", "odoo_company_name")
    op.drop_column("ai_connected_accounts", "odoo_company_id")
    op.drop_table("ai_memories")
