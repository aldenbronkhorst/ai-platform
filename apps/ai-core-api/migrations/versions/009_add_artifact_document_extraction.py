"""Add document extraction fields to artifacts.

Revision ID: 009_doc_extract
Revises: 008_add_usage_trace_correlation
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa

revision = "009_doc_extract"
down_revision = "008_add_usage_trace_correlation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("ALTER TABLE ai_artifacts ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(30) NOT NULL DEFAULT 'not_required'"))
    op.execute(sa.text("ALTER TABLE ai_artifacts ADD COLUMN IF NOT EXISTS extraction_source VARCHAR(100)"))
    op.execute(sa.text("ALTER TABLE ai_artifacts ADD COLUMN IF NOT EXISTS extracted_text TEXT"))
    op.execute(sa.text("ALTER TABLE ai_artifacts ADD COLUMN IF NOT EXISTS extraction_metadata_json JSON"))
    op.execute(sa.text("ALTER TABLE ai_artifacts ADD COLUMN IF NOT EXISTS extraction_error TEXT"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_artifacts_extraction_status ON ai_artifacts (extraction_status)"))


def downgrade():
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ai_artifacts_extraction_status"))
    op.execute(sa.text("ALTER TABLE ai_artifacts DROP COLUMN IF EXISTS extraction_error"))
    op.execute(sa.text("ALTER TABLE ai_artifacts DROP COLUMN IF EXISTS extraction_metadata_json"))
    op.execute(sa.text("ALTER TABLE ai_artifacts DROP COLUMN IF EXISTS extracted_text"))
    op.execute(sa.text("ALTER TABLE ai_artifacts DROP COLUMN IF EXISTS extraction_source"))
    op.execute(sa.text("ALTER TABLE ai_artifacts DROP COLUMN IF EXISTS extraction_status"))
