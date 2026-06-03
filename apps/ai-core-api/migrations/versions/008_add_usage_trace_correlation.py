"""Add trace correlation fields to usage logs.

Revision ID: 008_add_usage_trace_correlation
Revises: 007_remove_chat_workflow_context
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "008_add_usage_trace_correlation"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("ALTER TABLE ai_usage_logs ADD COLUMN IF NOT EXISTS request_id VARCHAR(100)"))
    op.execute(sa.text("ALTER TABLE ai_usage_logs ADD COLUMN IF NOT EXISTS trace_id VARCHAR(100)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_request_id ON ai_usage_logs (request_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_trace_id ON ai_usage_logs (trace_id)"))


def downgrade():
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ai_usage_logs_trace_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ai_usage_logs_request_id"))
    op.execute(sa.text("ALTER TABLE ai_usage_logs DROP COLUMN IF EXISTS trace_id"))
    op.execute(sa.text("ALTER TABLE ai_usage_logs DROP COLUMN IF EXISTS request_id"))
