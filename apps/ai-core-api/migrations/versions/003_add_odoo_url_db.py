"""add odoo_url and odoo_db columns to ai_connected_accounts

Revision ID: 003_add_odoo_url_db
Revises: 002_providers_and_usage
Create Date: 2026-05-30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003_add_odoo_url_db"
down_revision: Union[str, None] = "002_providers_and_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_connected_accounts", sa.Column("odoo_url", sa.String(500), nullable=True))
    op.add_column("ai_connected_accounts", sa.Column("odoo_db", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_connected_accounts", "odoo_db")
    op.drop_column("ai_connected_accounts", "odoo_url")
