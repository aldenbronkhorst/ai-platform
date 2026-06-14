"""Compatibility bridge for old Microsoft device auth revision.

Revision ID: 014_ms_device_auth_provider
Revises: 002_ms_device_auth
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "014_ms_device_auth_provider"
down_revision: Union[str, None] = "002_ms_device_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_microsoft_device_auth_sessions"
OLD_CONSTRAINT = "uq_ms_device_auth_user"
NEW_CONSTRAINT = "uq_ms_device_auth_user_provider"


def _unique_constraint_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE_NAME):
        return set()
    return {constraint["name"] for constraint in inspector.get_unique_constraints(TABLE_NAME)}


def upgrade() -> None:
    constraints = _unique_constraint_names()
    if NEW_CONSTRAINT in constraints:
        return
    if OLD_CONSTRAINT in constraints:
        op.drop_constraint(OLD_CONSTRAINT, TABLE_NAME, type_="unique")
    op.create_unique_constraint(
        NEW_CONSTRAINT,
        TABLE_NAME,
        ["user_id", "provider"],
    )


def downgrade() -> None:
    constraints = _unique_constraint_names()
    if OLD_CONSTRAINT in constraints:
        return
    if NEW_CONSTRAINT in constraints:
        op.drop_constraint(NEW_CONSTRAINT, TABLE_NAME, type_="unique")
    op.create_unique_constraint(
        OLD_CONSTRAINT,
        TABLE_NAME,
        ["user_id"],
    )
