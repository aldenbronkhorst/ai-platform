"""add provider/model/route/usage tables idempotently

Revision ID: 002_providers_and_usage
Revises: 001_initial
Create Date: 2026-05-29

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002_providers_and_usage"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_providers (
            id UUID PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            provider_type VARCHAR(50) NOT NULL,
            base_url VARCHAR(500) NOT NULL,
            auth_type VARCHAR(30) NOT NULL DEFAULT 'key_vault_secret',
            secret_reference VARCHAR(500),
            enabled VARCHAR(10) NOT NULL DEFAULT 'true',
            capabilities JSONB,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_providers_name ON ai_providers (name)"))
    
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_models (
            id UUID PRIMARY KEY,
            provider_id UUID NOT NULL REFERENCES ai_providers(id),
            display_name VARCHAR(255) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            deployment_name VARCHAR(255) NOT NULL,
            model_family VARCHAR(100),
            model_version VARCHAR(100),
            supports_tools VARCHAR(10) NOT NULL DEFAULT 'false',
            supports_json_schema VARCHAR(10) NOT NULL DEFAULT 'false',
            context_window INTEGER,
            enabled VARCHAR(10) NOT NULL DEFAULT 'true',
            config_json JSONB,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_models_provider_id ON ai_models (provider_id)"))
    
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_routes (
            id UUID PRIMARY KEY,
            task_type VARCHAR(100) NOT NULL UNIQUE,
            primary_model_id UUID NOT NULL REFERENCES ai_models(id),
            fallback_model_id UUID REFERENCES ai_models(id),
            temperature NUMERIC(4,2) NOT NULL DEFAULT 0.3,
            max_tokens INTEGER NOT NULL DEFAULT 2000,
            system_prompt TEXT,
            enabled VARCHAR(10) NOT NULL DEFAULT 'true',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_routes_task_type ON ai_routes (task_type)"))
    
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_usage_logs (
            id UUID PRIMARY KEY,
            timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
            provider_id UUID REFERENCES ai_providers(id),
            model_id UUID REFERENCES ai_models(id),
            route_id UUID REFERENCES ai_routes(id),
            task_type VARCHAR(100),
            chat_session_id UUID REFERENCES ai_chat_sessions(id),
            user_id UUID REFERENCES ai_users(id),
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER,
            cost_estimate NUMERIC(12,6),
            status VARCHAR(20) NOT NULL DEFAULT 'success',
            error_message TEXT
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_timestamp ON ai_usage_logs (timestamp)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_user_id ON ai_usage_logs (user_id)"))


def downgrade() -> None:
    op.drop_table("ai_usage_logs")
    op.drop_table("ai_routes")
    op.drop_table("ai_models")
    op.drop_table("ai_providers")
