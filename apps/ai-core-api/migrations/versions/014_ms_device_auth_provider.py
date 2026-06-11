"""Scope Microsoft device auth sessions by provider.

Revision ID: 014_ms_device_auth_provider
Revises: 013_ms_device_auth
Create Date: 2026-06-11
"""
from __future__ import annotations

from alembic import op


revision = "014_ms_device_auth_provider"
down_revision = "013_ms_device_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_ms_device_auth_user", "ai_microsoft_device_auth_sessions", type_="unique")
    op.create_unique_constraint(
        "uq_ms_device_auth_user_provider",
        "ai_microsoft_device_auth_sessions",
        ["user_id", "provider"],
    )


def downgrade():
    op.drop_constraint("uq_ms_device_auth_user_provider", "ai_microsoft_device_auth_sessions", type_="unique")
    op.create_unique_constraint(
        "uq_ms_device_auth_user",
        "ai_microsoft_device_auth_sessions",
        ["user_id"],
    )
