"""Split Microsoft Admin into native Microsoft tool connectors.

Revision ID: 012_split_ms_native
Revises: 011_ms_admin_cleanup
Create Date: 2026-06-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "012_split_ms_native"
down_revision = "011_ms_admin_cleanup"
branch_labels = None
depends_on = None


TOOL_TARGET_SYSTEMS = {
    "ms_azure_cli": "azure_cli",
    "ms_az_powershell": "azure_cli",
    "ms_bicep": "azure_cli",
    "ms_graph": "microsoft_graph",
    "ms_graph_powershell": "microsoft_graph",
    "ms_exchange_powershell": "exchange_online",
    "ms_teams_powershell": "teams_admin",
    "ms_sharepoint_pnp_powershell": "sharepoint_pnp",
}


def upgrade():
    bind = op.get_bind()
    for tool_name, target_system in TOOL_TARGET_SYSTEMS.items():
        bind.execute(
            sa.text(
                "UPDATE ai_tools SET target_system=:target_system, status='active', updated_at=now() "
                "WHERE name=:tool_name"
            ),
            {"tool_name": tool_name, "target_system": target_system},
        )

    bind.execute(
        sa.text(
            "UPDATE ai_tools SET status='archived', updated_at=now() "
            "WHERE target_system='microsoft_admin' AND name NOT IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(TOOL_TARGET_SYSTEMS)},
    )
    bind.execute(
        sa.text(
            "UPDATE ai_connected_accounts "
            "SET status='disconnected', secret_reference=NULL, "
            "permission_summary='Retired: Microsoft Admin was split into native Microsoft tool connectors.', "
            "disconnected_at=now(), updated_at=now() "
            "WHERE provider='microsoft_admin'"
        )
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE ai_tools SET target_system='microsoft_admin', status='active', updated_at=now() "
            "WHERE name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(TOOL_TARGET_SYSTEMS)},
    )
