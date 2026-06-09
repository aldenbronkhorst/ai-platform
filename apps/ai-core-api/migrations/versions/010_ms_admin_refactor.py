"""Refactor Microsoft Admin connector naming.

Revision ID: 010_ms_admin_refactor
Revises: 009_doc_extract
Create Date: 2026-06-09
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "010_ms_admin_refactor"
down_revision = "009_doc_extract"
branch_labels = None
depends_on = None


MICROSOFT_ADMIN_TOOLS = [
    (
        "ms_graph",
        "Microsoft Graph",
        "Direct Microsoft Graph interface for Entra, Microsoft 365, Intune, users, groups, licensing, and directory APIs.",
        {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT", "DELETE"], "default": "GET"},
                "path": {"type": "string"},
                "api_version": {"type": "string", "enum": ["v1.0", "beta"], "default": "v1.0"},
                "body": {"type": "object"},
                "headers": {"type": "object"},
                "max_pages": {"type": "integer", "default": 20},
                "max_items": {"type": "integer", "default": 1000},
                "purpose": {"type": "string"},
            },
            "required": ["method", "path"],
        },
    ),
    (
        "ms_graph_powershell",
        "Microsoft Graph PowerShell",
        "Microsoft Graph PowerShell interface for Entra, users, groups, licensing, roles, and Intune administration.",
        {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["script"]},
    ),
    (
        "ms_exchange_powershell",
        "Exchange Online PowerShell",
        "Exchange Online PowerShell interface for mailboxes, mail flow, permissions, transport rules, and message trace.",
        {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["script"]},
    ),
    (
        "ms_teams_powershell",
        "Microsoft Teams PowerShell",
        "Microsoft Teams PowerShell interface for Teams admin work and policies.",
        {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["script"]},
    ),
    (
        "ms_sharepoint_pnp_powershell",
        "SharePoint PnP PowerShell",
        "SharePoint and PnP PowerShell interface for SharePoint admin and site automation.",
        {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["script"]},
    ),
    (
        "ms_az_powershell",
        "Azure PowerShell",
        "Az PowerShell interface for Azure Resource Manager resources, RBAC, deployments, and operations.",
        {"type": "object", "properties": {"script": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["script"]},
    ),
    (
        "ms_azure_cli",
        "Azure Resource Manager CLI",
        "Azure CLI interface inside Microsoft Admin for Azure Resource Manager, Cost Management via az rest, resources, RBAC, and logs.",
        {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["command"]},
    ),
    (
        "ms_bicep",
        "Microsoft Bicep CLI",
        "Bicep CLI interface for build, decompile, format, lint, and template validation workflows.",
        {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 60}, "purpose": {"type": "string"}}, "required": ["command"]},
    ),
]


def upgrade():
    op.execute(sa.text("UPDATE ai_connected_accounts SET provider='microsoft_admin', updated_at=now() WHERE provider='azure'"))
    op.execute(sa.text(
        "UPDATE ai_connected_accounts "
        "SET secret_reference=replace(secret_reference, 'connector-token-azure-', 'connector-token-microsoft_admin-'), updated_at=now() "
        "WHERE secret_reference LIKE 'connector-token-azure-%'"
    ))
    op.execute(sa.text("UPDATE ai_tools SET target_system='microsoft_admin', updated_at=now() WHERE target_system='azure'"))
    op.execute(sa.text("UPDATE ai_audit_events SET target_system='microsoft_admin' WHERE target_system='azure'"))
    op.execute(sa.text("UPDATE ai_tasks SET linked_system='microsoft_admin', updated_at=now() WHERE linked_system='azure'"))
    op.execute(sa.text("UPDATE ai_jobs SET linked_system='microsoft_admin', updated_at=now() WHERE linked_system='azure'"))
    op.execute(sa.text("UPDATE ai_tools SET status='archived', updated_at=now() WHERE name IN ('ms_admin', 'ms_powershell', 'azure_cli')"))

    bind = op.get_bind()
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


def downgrade():
    op.execute(sa.text("UPDATE ai_connected_accounts SET provider='azure', updated_at=now() WHERE provider='microsoft_admin'"))
    op.execute(sa.text(
        "UPDATE ai_connected_accounts "
        "SET secret_reference=replace(secret_reference, 'connector-token-microsoft_admin-', 'connector-token-azure-'), updated_at=now() "
        "WHERE secret_reference LIKE 'connector-token-microsoft_admin-%'"
    ))
    op.execute(sa.text("UPDATE ai_tools SET target_system='azure', updated_at=now() WHERE target_system='microsoft_admin'"))
    op.execute(sa.text("UPDATE ai_audit_events SET target_system='azure' WHERE target_system='microsoft_admin'"))
    op.execute(sa.text("UPDATE ai_tasks SET linked_system='azure', updated_at=now() WHERE linked_system='microsoft_admin'"))
    op.execute(sa.text("UPDATE ai_jobs SET linked_system='azure', updated_at=now() WHERE linked_system='microsoft_admin'"))
    op.execute(sa.text("UPDATE ai_tools SET status='archived', updated_at=now() WHERE name IN ('ms_graph_powershell', 'ms_exchange_powershell', 'ms_teams_powershell', 'ms_sharepoint_pnp_powershell', 'ms_az_powershell')"))
