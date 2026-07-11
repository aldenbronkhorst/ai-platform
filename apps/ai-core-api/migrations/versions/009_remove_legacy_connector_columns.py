"""Remove connector-specific columns from connected accounts.

Revision ID: 009_remove_legacy_connector_columns
Revises: 008_durable_chat_queue
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "009_remove_legacy_connector_columns"
down_revision: Union[str, None] = "008_durable_chat_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ai_connected_accounts", "odoo_currency_symbol")
    op.drop_column("ai_connected_accounts", "odoo_currency_code")
    op.drop_column("ai_connected_accounts", "odoo_company_name")
    op.drop_column("ai_connected_accounts", "odoo_company_id")
    op.drop_column("ai_connected_accounts", "odoo_db")
    op.drop_column("ai_connected_accounts", "odoo_url")


def downgrade() -> None:
    op.add_column("ai_connected_accounts", sa.Column("odoo_url", sa.String(length=500), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("odoo_db", sa.String(length=255), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("odoo_company_id", sa.Integer(), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("odoo_company_name", sa.String(length=255), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("odoo_currency_code", sa.String(length=10), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("odoo_currency_symbol", sa.String(length=10), nullable=True))
    op.execute(
        """
        UPDATE ai_connected_accounts
        SET odoo_url = configuration_json ->> 'url',
            odoo_db = configuration_json ->> 'db',
            odoo_company_id = NULLIF(connector_metadata_json ->> 'company_id', '')::integer,
            odoo_company_name = connector_metadata_json ->> 'company_name',
            odoo_currency_code = connector_metadata_json ->> 'currency_code',
            odoo_currency_symbol = connector_metadata_json ->> 'currency_symbol'
        WHERE provider = 'odoo'
        """
    )
