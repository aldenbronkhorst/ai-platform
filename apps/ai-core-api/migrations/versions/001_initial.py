"""Initial migration: create core AI Platform tables

Revision ID: 001_initial
Revises: 
Create Date: 2026-05-28 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.String(255), unique=True, nullable=False, index=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('entra_object_id', sa.String(255), unique=True, nullable=True),
        sa.Column('role', sa.String(50), default='user', nullable=False),
        sa.Column('department', sa.String(100), nullable=True),
        sa.Column('is_active', sa.String(10), default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_connected_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=False, index=True),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('provider_user_id', sa.String(255), nullable=True),
        sa.Column('provider_username', sa.String(255), nullable=True),
        sa.Column('scopes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), default='active', nullable=False),
        sa.Column('secret_reference', sa.String(500), nullable=True),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_company_facts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('key', sa.String(255), unique=True, nullable=False, index=True),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('source', sa.String(255), nullable=True),
        sa.Column('confidence', sa.String(20), default='high', nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('scope_type', sa.String(50), nullable=True),
        sa.Column('scope_value', sa.String(255), nullable=True),
        sa.Column('department', sa.String(100), nullable=True),
        sa.Column('workflow', sa.String(100), nullable=True),
        sa.Column('supplier', sa.String(255), nullable=True),
        sa.Column('customer', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), default='active', nullable=False),
        sa.Column('priority', sa.Integer(), default=100, nullable=False),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('approved_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('effective_from', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('supersedes_rule_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_rules.id'), nullable=True),
        sa.Column('version', sa.Integer(), default=1, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), default='open', nullable=False),
        sa.Column('priority', sa.String(20), default='medium', nullable=False),
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True, index=True),
        sa.Column('department', sa.String(100), nullable=True),
        sa.Column('linked_system', sa.String(50), nullable=True),
        sa.Column('linked_model', sa.String(100), nullable=True),
        sa.Column('linked_record_id', sa.String(100), nullable=True),
        sa.Column('created_from_conversation_id', sa.String(255), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('next_review_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completion_check_type', sa.String(50), nullable=True),
        sa.Column('completion_check_payload', sa.JSON(), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workflow_type', sa.String(100), nullable=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('status', sa.String(20), default='pending', nullable=False),
        sa.Column('requested_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True, index=True),
        sa.Column('identity_mode', sa.String(30), default='user-delegated', nullable=False),
        sa.Column('linked_system', sa.String(50), nullable=True),
        sa.Column('linked_model', sa.String(100), nullable=True),
        sa.Column('linked_record_id', sa.String(100), nullable=True),
        sa.Column('current_step', sa.String(100), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_artifacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_jobs.id'), nullable=True, index=True),
        sa.Column('artifact_type', sa.String(50), nullable=False),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('storage_uri', sa.String(1000), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('source_tool', sa.String(100), nullable=True),
        sa.Column('stage', sa.String(50), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('retention_policy', sa.String(20), default='standard', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_chat_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=False, index=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('workflow_context', sa.String(100), nullable=True),
        sa.Column('last_message_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('chat_session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_chat_sessions.id'), nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=False, index=True),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('model_provider', sa.String(100), nullable=True),
        sa.Column('model_name', sa.String(100), nullable=True),
        sa.Column('token_usage_json', sa.JSON(), nullable=True),
        sa.Column('tool_call_json', sa.JSON(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_tools',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), unique=True, nullable=False, index=True),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('target_system', sa.String(50), nullable=False),
        sa.Column('input_schema', sa.JSON(), nullable=True),
        sa.Column('output_schema', sa.JSON(), nullable=True),
        sa.Column('version', sa.String(20), default='1.0.0', nullable=False),
        sa.Column('status', sa.String(20), default='active', nullable=False),
        sa.Column('requires_approval', sa.String(10), default='false', nullable=False),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'ai_audit_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, index=True),
        sa.Column('actor_type', sa.String(20), default='user', nullable=False),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_users.id'), nullable=True),
        sa.Column('identity_mode', sa.String(30), default='user-delegated', nullable=False),
        sa.Column('interface', sa.String(50), nullable=True),
        sa.Column('action_type', sa.String(50), nullable=False),
        sa.Column('tool_name', sa.String(100), nullable=True),
        sa.Column('target_system', sa.String(50), nullable=True),
        sa.Column('target_model', sa.String(100), nullable=True),
        sa.Column('target_record_id', sa.String(100), nullable=True),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ai_jobs.id'), nullable=True, index=True),
        sa.Column('input_summary', sa.Text(), nullable=True),
        sa.Column('output_summary', sa.Text(), nullable=True),
        sa.Column('risk_level', sa.String(20), default='low', nullable=False),
        sa.Column('status', sa.String(20), default='success', nullable=False),
        sa.Column('cost_estimate', sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('ai_audit_events')
    op.drop_table('ai_tools')
    op.drop_table('ai_chat_messages')
    op.drop_table('ai_chat_sessions')
    op.drop_table('ai_artifacts')
    op.drop_table('ai_jobs')
    op.drop_table('ai_tasks')
    op.drop_table('ai_rules')
    op.drop_table('ai_company_facts')
    op.drop_table('ai_connected_accounts')
    op.drop_table('ai_users')
