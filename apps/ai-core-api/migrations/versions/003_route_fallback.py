"""Add route fallback model.

Revision ID: 003_route_fallback
Revises: 002_ms_device_auth
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "003_route_fallback"
down_revision: Union[str, None] = "002_ms_device_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_routes"
COLUMN_NAME = "fallback_model_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME in columns:
        return

    uuid_type = sa.String(length=36) if bind.dialect.name == "sqlite" else postgresql.UUID(as_uuid=True)
    op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, uuid_type, nullable=True))
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_ai_routes_fallback_model_id_ai_models",
            TABLE_NAME,
            "ai_models",
            [COLUMN_NAME],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME not in columns:
        return

    if bind.dialect.name != "sqlite":
        op.drop_constraint("fk_ai_routes_fallback_model_id_ai_models", TABLE_NAME, type_="foreignkey")
    op.drop_column(TABLE_NAME, COLUMN_NAME)
