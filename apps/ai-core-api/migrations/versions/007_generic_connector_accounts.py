"""Expand connected accounts with connector-owned data.

Revision ID: 007_generic_connector_accounts
Revises: 006_chat_event_stream
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_generic_connector_accounts"
down_revision: Union[str, None] = "006_chat_event_stream"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_connected_accounts", sa.Column("configuration_json", sa.JSON(), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("connector_metadata_json", sa.JSON(), nullable=True))

    op.execute(
        """
        UPDATE ai_connected_accounts
        SET configuration_json = json_build_object(
                'url', odoo_url,
                'db', odoo_db,
                'username', provider_username
            ),
            connector_metadata_json = json_build_object(
                'company_id', odoo_company_id,
                'company_name', odoo_company_name,
                'currency_code', odoo_currency_code,
                'currency_symbol', odoo_currency_symbol
            )
        WHERE provider = 'odoo'
        """
    )

def downgrade() -> None:
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

    op.drop_column("ai_connected_accounts", "connector_metadata_json")
    op.drop_column("ai_connected_accounts", "configuration_json")
