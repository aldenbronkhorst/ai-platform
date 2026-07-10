import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, String, DateTime, Text, Integer, ForeignKey, JSON, Numeric, Index, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class AuditMixin:
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


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
    provider = Column(String(50), nullable=False)
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


class AIArtifact(Base, AuditMixin):
    __tablename__ = "ai_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_type = Column(String(50), default="chat-upload", nullable=False)
    filename = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False)
    storage_uri = Column(String(1000), nullable=False)
    sha256 = Column(String(64), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)
    extraction_status = Column(String(30), default="not_required", nullable=False)
    extraction_source = Column(String(100), nullable=True)
    extracted_text = Column(Text, nullable=True)
    extraction_metadata_json = Column(JSON, nullable=True)
    extraction_error = Column(Text, nullable=True)


class AITool(Base, AuditMixin):
    __tablename__ = "ai_tools"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    target_system = Column(String(50), nullable=False)
    input_schema = Column(JSON, nullable=True)
    output_schema = Column(JSON, nullable=True)
    version = Column(String(20), default="1.0.0", nullable=False)
    status = Column(String(20), default="active", nullable=False)
    requires_approval = Column(String(10), default="false", nullable=False)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True)


class AIChatSession(Base, AuditMixin):
    __tablename__ = "ai_chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    status = Column(String(20), default="active", nullable=False)  # active, archived, deleted
    last_message_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
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


class AIChatEvent(Base):
    __tablename__ = "ai_chat_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=False, index=True)
    request_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)


class AIChatTurn(Base):
    __tablename__ = "ai_chat_turns"
    __table_args__ = (
        Index(
            "uq_ai_chat_turns_active_session",
            "chat_session_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    request_id = Column(String(100), primary_key=True)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=False, index=True)
    status = Column(String(20), default="active", nullable=False, index=True)
    cancel_requested = Column(Boolean, default=False, nullable=False)
    started_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False, index=True)


class AIChatArtifact(Base, AuditMixin):
    __tablename__ = "ai_chat_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("ai_artifacts.id"), nullable=False, index=True)
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
    timestamp = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    request_id = Column(String(100), nullable=True, index=True)
    trace_id = Column(String(100), nullable=True, index=True)
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
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AITrace(Base):
    __tablename__ = "ai_traces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id = Column(String(100), unique=True, nullable=False, index=True)
    request_id = Column(String(100), nullable=False, index=True)
    operation_type = Column(String(50), nullable=False, index=True)
    operation_name = Column(String(200), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    user_id = Column(UUID(as_uuid=True), ForeignKey("ai_users.id"), nullable=True, index=True)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id"), nullable=True, index=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("ai_chat_messages.id"), nullable=True)
    connector = Column(String(50), nullable=True)
    provider = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    route_id = Column(UUID(as_uuid=True), ForeignKey("ai_routes.id"), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_type = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AITraceSpan(Base):
    __tablename__ = "ai_trace_spans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id = Column(String(100), ForeignKey("ai_traces.trace_id"), nullable=False, index=True)
    span_id = Column(String(100), nullable=False)
    parent_span_id = Column(String(100), nullable=True)
    span_type = Column(String(50), nullable=False)
    span_name = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    input_summary_json = Column(JSON, nullable=True)
    output_summary_json = Column(JSON, nullable=True)
    error_type = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
