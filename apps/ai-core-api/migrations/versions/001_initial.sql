-- Initial migration: create core AI Platform tables
-- Run this with: psql -h <host> -U <user> -d <db> -f 001_initial.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS ai_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    entra_object_id VARCHAR(255) UNIQUE,
    role VARCHAR(50) DEFAULT 'user' NOT NULL,
    department VARCHAR(100),
    is_active VARCHAR(10) DEFAULT 'true' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_users_email ON ai_users(email);

CREATE TABLE IF NOT EXISTS ai_connected_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES ai_users(id),
    provider VARCHAR(50) NOT NULL,
    provider_user_id VARCHAR(255),
    provider_username VARCHAR(255),
    scopes TEXT,
    status VARCHAR(20) DEFAULT 'active' NOT NULL,
    secret_reference VARCHAR(500),
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_connected_accounts_user_id ON ai_connected_accounts(user_id);

CREATE TABLE IF NOT EXISTS ai_company_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(255) UNIQUE NOT NULL,
    value TEXT NOT NULL,
    category VARCHAR(100),
    source VARCHAR(255),
    confidence VARCHAR(20) DEFAULT 'high' NOT NULL,
    effective_from TIMESTAMPTZ,
    effective_to TIMESTAMPTZ,
    created_by_user_id UUID REFERENCES ai_users(id),
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_company_facts_key ON ai_company_facts(key);

CREATE TABLE IF NOT EXISTS ai_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    body TEXT NOT NULL,
    scope_type VARCHAR(50),
    scope_value VARCHAR(255),
    department VARCHAR(100),
    workflow VARCHAR(100),
    supplier VARCHAR(255),
    customer VARCHAR(255),
    status VARCHAR(20) DEFAULT 'active' NOT NULL,
    priority INTEGER DEFAULT 100 NOT NULL,
    created_by_user_id UUID REFERENCES ai_users(id),
    approved_by_user_id UUID REFERENCES ai_users(id),
    effective_from TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    effective_to TIMESTAMPTZ,
    supersedes_rule_id UUID REFERENCES ai_rules(id),
    version INTEGER DEFAULT 1 NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(20) DEFAULT 'open' NOT NULL,
    priority VARCHAR(20) DEFAULT 'medium' NOT NULL,
    owner_user_id UUID REFERENCES ai_users(id),
    department VARCHAR(100),
    linked_system VARCHAR(50),
    linked_model VARCHAR(100),
    linked_record_id VARCHAR(100),
    created_from_conversation_id VARCHAR(255),
    created_by_user_id UUID REFERENCES ai_users(id),
    next_review_at TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    completion_check_type VARCHAR(50),
    completion_check_payload JSONB,
    last_checked_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_tasks_owner_user_id ON ai_tasks(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_ai_tasks_status ON ai_tasks(status);

CREATE TABLE IF NOT EXISTS ai_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_type VARCHAR(100),
    title VARCHAR(500) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' NOT NULL,
    requested_by_user_id UUID REFERENCES ai_users(id),
    identity_mode VARCHAR(30) DEFAULT 'user-delegated' NOT NULL,
    linked_system VARCHAR(50),
    linked_model VARCHAR(100),
    linked_record_id VARCHAR(100),
    current_step VARCHAR(100),
    summary TEXT,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_requested_by_user_id ON ai_jobs(requested_by_user_id);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);

CREATE TABLE IF NOT EXISTS ai_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES ai_jobs(id),
    artifact_type VARCHAR(50) NOT NULL,
    filename VARCHAR(500) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    storage_uri VARCHAR(1000) NOT NULL,
    sha256 VARCHAR(64),
    source_tool VARCHAR(100),
    stage VARCHAR(50),
    created_by_user_id UUID REFERENCES ai_users(id),
    retention_policy VARCHAR(20) DEFAULT 'standard' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_artifacts_job_id ON ai_artifacts(job_id);

CREATE TABLE IF NOT EXISTS ai_tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    description TEXT,
    target_system VARCHAR(50) NOT NULL,
    input_schema JSONB,
    output_schema JSONB,
    version VARCHAR(20) DEFAULT '1.0.0' NOT NULL,
    status VARCHAR(20) DEFAULT 'active' NOT NULL,
    requires_approval VARCHAR(10) DEFAULT 'false' NOT NULL,
    created_by_user_id UUID REFERENCES ai_users(id),
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_tools_name ON ai_tools(name);

CREATE TABLE IF NOT EXISTS ai_audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    actor_type VARCHAR(20) DEFAULT 'user' NOT NULL,
    actor_user_id UUID REFERENCES ai_users(id),
    identity_mode VARCHAR(30) DEFAULT 'user-delegated' NOT NULL,
    interface VARCHAR(50),
    action_type VARCHAR(50) NOT NULL,
    tool_name VARCHAR(100),
    target_system VARCHAR(50),
    target_model VARCHAR(100),
    target_record_id VARCHAR(100),
    job_id UUID REFERENCES ai_jobs(id),
    input_summary TEXT,
    output_summary TEXT,
    risk_level VARCHAR(20) DEFAULT 'low' NOT NULL,
    status VARCHAR(20) DEFAULT 'success' NOT NULL,
    cost_estimate NUMERIC(10, 4)
);

CREATE INDEX IF NOT EXISTS idx_ai_audit_events_timestamp ON ai_audit_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_ai_audit_events_job_id ON ai_audit_events(job_id);

-- Create alembic version table to track migrations
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);

INSERT INTO alembic_version (version_num) VALUES ('001_initial')
ON CONFLICT (version_num) DO NOTHING;
