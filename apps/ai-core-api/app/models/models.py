import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, JSON, Numeric
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.core.database import Base


class AuditMixin:
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AIUser(Base, AuditMixin):
    __tablename__ = "ai_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=True)
    entra_object_id = Column(String(255), unique=True, nullable=True)
    role = Column(String(50), default="user", nullable=False)
    department = Column(String(100), nullable=True)
    is_active = Column(String(10), default="true", nullable=False)


class AIConnectedAccount(Base, AuditMixin):
    __tablename__ = "ai_connected_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=False, index=True)
    provider = Column(String(50), nullable=False)  # microsoft, odoo, github, azure
    provider_user_id = Column(String(255), nullable=True)
    provider_username = Column(String(255), nullable=True)
    provider_display_name = Column(String(255), nullable=True)
    scopes = Column(Text, nullable=True)
    status = Column(String(20), default="active", nullable=False)
    secret_reference = Column(String(500), nullable=True)  # Key Vault secret name
    target_environment = Column(String(50), default="production", nullable=True)
    permission_summary = Column(Text, nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    odoo_url = Column(String(500), nullable=True)
    odoo_db = Column(String(255), nullable=True)
    odoo_company_id = Column(Integer, nullable=True)
    odoo_company_name = Column(String(255), nullable=True)
    odoo_currency_code = Column(String(10), nullable=True)
    odoo_currency_symbol = Column(String(10), nullable=True)


class AICompanyFact(Base, AuditMixin):
    __tablename__ = "ai_company_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    category = Column(String(100), nullable=True)
    source = Column(String(255), nullable=True)
    confidence = Column(String(20), default="high", nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)


class AIRule(Base, AuditMixin):
    __tablename__ = "ai_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    scope_type = Column(String(50), nullable=True)  # global, department, workflow, supplier, customer
    scope_value = Column(String(255), nullable=True)
    department = Column(String(100), nullable=True)
    workflow = Column(String(100), nullable=True)
    supplier = Column(String(255), nullable=True)
    customer = Column(String(255), nullable=True)
    status = Column(String(20), default="active", nullable=False)
    priority = Column(Integer, default=100, nullable=False)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    approved_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    effective_from = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    supersedes_rule_id = Column(UUID(as_uuid=True), ForeignKey("ai_rules.id"), nullable=True)
    version = Column(Integer, default=1, nullable=False)


class AITask(Base, AuditMixin):
    __tablename__ = "ai_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default="open", nullable=False)  # open, in_progress, done, cancelled
    priority = Column(String(20), default="medium", nullable=False)  # low, medium, high, critical
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True, index=True)
    department = Column(String(100), nullable=True)
    linked_system = Column(String(50), nullable=True)  # odoo, github, azure, etc.
    linked_model = Column(String(100), nullable=True)
    linked_record_id = Column(String(100), nullable=True)
    created_from_conversation_id = Column(String(255), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    next_review_at = Column(DateTime(timezone=True), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True)
    completion_check_type = Column(String(50), nullable=True)
    completion_check_payload = Column(JSON, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)


class AIJob(Base, AuditMixin):
    __tablename__ = "ai_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_type = Column(String(100), nullable=True)
    title = Column(String(500), nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pending, running, completed, failed, cancelled
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True, index=True)
    identity_mode = Column(String(30), default="user-delegated", nullable=False)  # user-delegated, service-account
    linked_system = Column(String(50), nullable=True)
    linked_model = Column(String(100), nullable=True)
    linked_record_id = Column(String(100), nullable=True)
    current_step = Column(String(100), nullable=True)
    summary = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class AIArtifact(Base, AuditMixin):
    __tablename__ = "ai_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id"), nullable=True, index=True)
    artifact_type = Column(String(50), nullable=False)  # ocr, report, raw-export, debug, intermediate, final
    filename = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False)
    storage_uri = Column(String(1000), nullable=False)
    sha256 = Column(String(64), nullable=True)
    source_tool = Column(String(100), nullable=True)
    stage = Column(String(50), nullable=True)  # intermediate, final, debug
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    retention_policy = Column(String(20), default="standard", nullable=False)


class AITool(Base, AuditMixin):
    __tablename__ = "ai_tools"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    target_system = Column(String(50), nullable=False)  # odoo, github, azure, runner, ai-platform
    input_schema = Column(JSON, nullable=True)
    output_schema = Column(JSON, nullable=True)
    version = Column(String(20), default="1.0.0", nullable=False)
    status = Column(String(20), default="active", nullable=False)
    requires_approval = Column(String(10), default="false", nullable=False)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)


class AIAuditEvent(Base):
    __tablename__ = "ai_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    actor_type = Column(String(20), default="user", nullable=False)  # user, service, system
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    identity_mode = Column(String(30), default="user-delegated", nullable=False)
    interface = Column(String(50), nullable=True)  # chatgpt, claude, web, api
    action_type = Column(String(50), nullable=False)  # read, write, create, delete, tool_call, job_start
    tool_name = Column(String(100), nullable=True)
    target_system = Column(String(50), nullable=True)
    target_model = Column(String(100), nullable=True)
    target_record_id = Column(String(100), nullable=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id"), nullable=True, index=True)
    input_summary = Column(Text, nullable=True)
    output_summary = Column(Text, nullable=True)
    risk_level = Column(String(20), default="low", nullable=False)  # low, medium, high, critical
    status = Column(String(20), default="success", nullable=False)
    cost_estimate = Column(Numeric(10, 4), nullable=True)


class AIChatSession(Base, AuditMixin):
    __tablename__ = "ai_chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    status = Column(String(20), default="active", nullable=False)  # active, archived, deleted
    workflow_context = Column(String(100), nullable=True)
    last_message_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    metadata_json = Column(JSON, nullable=True)


class AIChatMessage(Base, AuditMixin):
    __tablename__ = "ai_chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user, assistant, system, tool
    content = Column(Text, nullable=False)
    model_provider = Column(String(100), nullable=True)
    model_name = Column(String(100), nullable=True)
    token_usage_json = Column(JSON, nullable=True)
    tool_call_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)


class AIChatArtifact(Base, AuditMixin):
    __tablename__ = "ai_chat_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("ai_artifacts.id"), nullable=False, index=True)
    linked_message_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_messages.id"), nullable=True, index=True)


class AIChatJob(Base, AuditMixin):
    __tablename__ = "ai_chat_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id"), nullable=False, index=True)
    linked_message_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_messages.id"), nullable=True, index=True)


class AIProvider(Base, AuditMixin):
    __tablename__ = "ai_providers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    provider_type = Column(String(50), nullable=False)
    base_url = Column(String(500), nullable=False)
    auth_type = Column(String(30), default="key_vault_secret", nullable=False)
    secret_reference = Column(String(500), nullable=True)
    enabled = Column(String(10), default="true", nullable=False)
    capabilities = Column(JSON, nullable=True)


class AIModel(Base, AuditMixin):
    __tablename__ = "ai_models"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("ai_providers.id"), nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    model_name = Column(String(255), nullable=False)
    deployment_name = Column(String(255), nullable=False)
    model_family = Column(String(100), nullable=True)
    model_version = Column(String(100), nullable=True)
    supports_tools = Column(String(10), default="false", nullable=False)
    supports_json_schema = Column(String(10), default="false", nullable=False)
    context_window = Column(Integer, nullable=True)
    enabled = Column(String(10), default="true", nullable=False)
    config_json = Column(JSON, nullable=True)


class AIRoute(Base, AuditMixin):
    __tablename__ = "ai_routes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type = Column(String(100), unique=True, nullable=False, index=True)
    primary_model_id = Column(UUID(as_uuid=True), ForeignKey("ai_models.id"), nullable=False)
    fallback_model_id = Column(UUID(as_uuid=True), ForeignKey("ai_models.id"), nullable=True)
    temperature = Column(Numeric(4, 2), default=0.3, nullable=False)
    max_tokens = Column(Integer, default=2000, nullable=False)
    system_prompt = Column(Text, nullable=True)
    enabled = Column(String(10), default="true", nullable=False)


class AIMemory(Base, AuditMixin):
    __tablename__ = "ai_memories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(50), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    summary = Column(String(1000), nullable=True)
    body = Column(Text, nullable=True)
    scope_type = Column(String(50), nullable=True)
    scope_value = Column(String(255), nullable=True)
    entities_json = Column(JSON, nullable=True)
    source_type = Column(String(50), nullable=True)
    source_id = Column(String(255), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=True, index=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_messages.id"), nullable=True)
    confidence = Column(String(20), default="medium", nullable=False)
    risk_level = Column(String(20), default="low", nullable=False)
    status = Column(String(20), default="draft", nullable=False, index=True)
    priority = Column(Integer, default=100, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    failure_count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    stale_after = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    approved_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    supersedes_memory_id = Column(UUID(as_uuid=True), ForeignKey("ai_memories.id"), nullable=True)
    version = Column(Integer, default=1, nullable=False)
    metadata_json = Column(JSON, nullable=True)


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("ai_providers.id"), nullable=True)
    model_id = Column(UUID(as_uuid=True), ForeignKey("ai_models.id"), nullable=True)
    route_id = Column(UUID(as_uuid=True), ForeignKey("ai_routes.id"), nullable=True)
    task_type = Column(String(100), nullable=True)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True, index=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, nullable=True)
    cost_estimate = Column(Numeric(12, 6), nullable=True)
    status = Column(String(20), default="success", nullable=False)
    error_message = Column(Text, nullable=True)


class AIMemoryUsageEvent(Base):
    __tablename__ = "ai_memory_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("ai_memories.id"), nullable=False, index=True)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=True, index=True)
    chat_message_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_messages.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True, index=True)
    request_id = Column(String(100), nullable=True)
    used_in_context = Column(String(10), default="true", nullable=False)  # "true" or "false"
    used_in_final_answer = Column(String(10), default="true", nullable=False)  # "true" or "false"
    feedback_type = Column(String(50), nullable=True)  # helpful, wrong, outdated, not_relevant, do_not_use, etc.
    feedback_value = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


