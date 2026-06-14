"""Disable legacy model provider.

Revision ID: 004_disable_legacy_provider
Revises: 003_route_fallback
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op


revision: str = "004_disable_legacy_provider"
down_revision: Union[str, None] = "003_route_fallback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE ai_providers SET enabled = 'false' WHERE provider_type = 'azure_foundry'")


def downgrade() -> None:
    pass
