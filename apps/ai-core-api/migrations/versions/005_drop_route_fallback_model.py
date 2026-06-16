"""Drop route fallback model.

Revision ID: 005_drop_route_fallback_model
Revises: 004_disable_legacy_provider
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005_drop_route_fallback_model"
down_revision: Union[str, None] = "004_disable_legacy_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_routes"
COLUMN_NAME = "fallback_model_id"
CONSTRAINT_NAME = "fk_ai_routes_fallback_model_id_ai_models"


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE_NAME):
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    if COLUMN_NAME not in _column_names():
        return

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        constraints = {
            constraint["name"]
            for constraint in sa.inspect(bind).get_foreign_keys(TABLE_NAME)
            if constraint.get("name")
        }
        if CONSTRAINT_NAME in constraints:
            op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="foreignkey")
    op.drop_column(TABLE_NAME, COLUMN_NAME)


def downgrade() -> None:
    if COLUMN_NAME in _column_names():
        return

    bind = op.get_bind()
    uuid_type = sa.String(length=36) if bind.dialect.name == "sqlite" else postgresql.UUID(as_uuid=True)
    op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, uuid_type, nullable=True))
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            CONSTRAINT_NAME,
            TABLE_NAME,
            "ai_models",
            [COLUMN_NAME],
            ["id"],
        )
