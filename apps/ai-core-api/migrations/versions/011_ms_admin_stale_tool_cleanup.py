"""Clean stale Microsoft Admin connector tool rows.

Revision ID: 011_ms_admin_cleanup
Revises: 010_ms_admin_refactor
Create Date: 2026-06-09
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "011_ms_admin_cleanup"
down_revision = "010_ms_admin_refactor"
branch_labels = None
depends_on = None


MICROSOFT_ADMIN_TOOLS = [
    ("ms_graph", "Microsoft Graph", "Direct Microsoft Graph interface for the Microsoft Admin connector.", {"type": "object", "properties": {"method": {"type": "string"}, "path": {"type": "string"}, "api_version": {"type": "string"}, "body": {"type": "object"}, "headers": {"type": "object"}, "max_pages": {"type": "integer"}, "max_items": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["method", "path"]}),
    ("ms_graph_powershell", "Microsoft Graph PowerShell", "Microsoft Graph PowerShell surface inside the Microsoft Admin connector.", {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["script"]}),
    ("ms_exchange_powershell", "Exchange Online PowerShell", "Exchange Online PowerShell surface inside the Microsoft Admin connector.", {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["script"]}),
    ("ms_teams_powershell", "Microsoft Teams PowerShell", "Microsoft Teams PowerShell surface inside the Microsoft Admin connector.", {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["script"]}),
    ("ms_sharepoint_pnp_powershell", "SharePoint PnP PowerShell", "SharePoint/PnP PowerShell surface inside the Microsoft Admin connector.", {"type": "object", "properties": {"script": {"type": "string"}, "site_url": {"type": "string"}, "admin_url": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["script"]}),
    ("ms_az_powershell", "Azure PowerShell", "Az PowerShell surface inside the Microsoft Admin connector.", {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["script"]}),
    ("ms_azure_cli", "Azure Resource Manager CLI", "Azure CLI surface inside the Microsoft Admin connector.", {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["command"]}),
    ("ms_bicep", "Microsoft Bicep CLI", "Bicep CLI interface for the Microsoft Admin connector.", {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}, "purpose": {"type": "string"}}, "required": ["command"]}),
]

MICROSOFT_ADMIN_TOOL_NAMES = tuple(name for name, *_ in MICROSOFT_ADMIN_TOOLS)
LEGACY_TOOL_NAMES = ("ms_admin", "ms_powershell", "azure_cli")


def upgrade():
    bind = op.get_bind()

    op.execute(sa.text("UPDATE ai_connected_accounts SET provider='microsoft_admin', updated_at=now() WHERE provider='azure'"))
    op.execute(sa.text(
        "UPDATE ai_connected_accounts "
        "SET secret_reference=replace(secret_reference, 'connector-token-azure-', 'connector-token-microsoft_admin-'), updated_at=now() "
        "WHERE secret_reference LIKE 'connector-token-azure-%'"
    ))
    op.execute(sa.text("UPDATE ai_audit_events SET target_system='microsoft_admin' WHERE target_system='azure'"))
    op.execute(sa.text("UPDATE ai_tasks SET linked_system='microsoft_admin', updated_at=now() WHERE linked_system='azure'"))
    op.execute(sa.text("UPDATE ai_jobs SET linked_system='microsoft_admin', updated_at=now() WHERE linked_system='azure'"))

    update_ms_tools = sa.text(
        "UPDATE ai_tools SET target_system='microsoft_admin', updated_at=now() WHERE name IN :names"
    ).bindparams(sa.bindparam("names", expanding=True))
    bind.execute(update_ms_tools, {"names": list(MICROSOFT_ADMIN_TOOL_NAMES)})

    upsert = sa.text(
        """
        INSERT INTO ai_tools (
            name, display_name, description, target_system, input_schema, version,
            status, requires_approval, created_at, updated_at
        )
        VALUES (
            :name, :display_name, :description, 'microsoft_admin', CAST(:input_schema AS JSON),
            '1.0.0', 'active', 'false', now(), now()
        )
        ON CONFLICT (name) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            target_system = EXCLUDED.target_system,
            input_schema = EXCLUDED.input_schema,
            status = 'active',
            updated_at = now()
        """
    )
    for name, display_name, description, input_schema in MICROSOFT_ADMIN_TOOLS:
        bind.execute(
            upsert,
            {
                "name": name,
                "display_name": display_name,
                "description": description,
                "input_schema": json.dumps(input_schema),
            },
        )

    archive_legacy_tools = sa.text(
        "UPDATE ai_tools SET status='archived', updated_at=now() WHERE name IN :names"
    ).bindparams(sa.bindparam("names", expanding=True))
    bind.execute(archive_legacy_tools, {"names": list(LEGACY_TOOL_NAMES)})

    archive_stale_azure_tools = sa.text(
        "UPDATE ai_tools SET status='archived', updated_at=now() "
        "WHERE target_system='azure' AND name NOT IN :names"
    ).bindparams(sa.bindparam("names", expanding=True))
    bind.execute(archive_stale_azure_tools, {"names": list(MICROSOFT_ADMIN_TOOL_NAMES)})


def downgrade():
    pass
