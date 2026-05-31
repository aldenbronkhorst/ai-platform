import os
import re
import json
import logging
from uuid import UUID
from datetime import datetime
from typing import Optional, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import httpx

from app.models.models import (
    AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount, AITool, AICompanyFact,
    AIMemory,
)
from app.services.foundry_client import FoundryClient
from app.services.context import ContextService
from app.schemas.schemas import ContextRequest

logger = logging.getLogger(__name__)

ROUTE_NOT_CONFIGURED_MESSAGE = "AI chat is not configured yet. Please ask an administrator to configure a model in Settings \u2192 AI Configuration."

CANONICAL_SYSTEM_PROMPT = (
    "You are the AI Platform for Lots Lots More. "
    "You help employees work across company knowledge, workflows, documents, "
    "tasks, connected accounts, and business systems. "
    "You are not tied to one system. "
    "You may use connected tools such as Odoo, GitHub, Azure, Microsoft 365, "
    "documents, and future connectors only when they are available, authorised, "
    "and relevant. "
    "Never claim live access to a system unless that connector is connected and "
    "permitted for the current user. "
    "If a required connector is not connected, explain that clearly and guide "
    "the user to Connected Accounts. "
    "Keep responses practical, business-focused, and clear."
)


class RouteNotFoundError(Exception):
    def __init__(self, task_type: str):
        self.task_type = task_type
        super().__init__(ROUTE_NOT_CONFIGURED_MESSAGE)


class ProviderCallError(Exception):
    def __init__(self, message: str, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(message)


async def get_enabled_route(db: AsyncSession, task_type: str = "general_chat") -> tuple:
    result = await db.execute(
        select(AIRoute).where(AIRoute.task_type == task_type, AIRoute.enabled == "true")
    )
    route = result.scalar_one_or_none()
    if not route:
        raise RouteNotFoundError(task_type)

    model_result = await db.execute(
        select(AIModel).where(AIModel.id == route.primary_model_id, AIModel.enabled == "true")
    )
    model = model_result.scalar_one_or_none()
    if not model:
        raise RouteNotFoundError(task_type)

    provider_result = await db.execute(
        select(AIProvider).where(AIProvider.id == model.provider_id, AIProvider.enabled == "true")
    )
    provider = provider_result.scalar_one_or_none()
    if not provider:
        raise RouteNotFoundError(task_type)

    return route, model, provider


_secret_client: Optional[SecretClient] = None


def _get_kv_client() -> SecretClient:
    global _secret_client
    if _secret_client is None:
        vault_uri = os.environ.get("KEY_VAULT_URI", "")
        if vault_uri:
            credential = DefaultAzureCredential()
            _secret_client = SecretClient(vault_url=vault_uri, credential=credential)
    return _secret_client


async def _resolve_api_key(provider: AIProvider) -> Optional[str]:
    """Try Key Vault secret first, then env var, then fall back to hard-coded."""
    if provider.auth_type == "key_vault_secret" and provider.secret_reference:
        try:
            client = _get_kv_client()
            if client:
                secret = client.get_secret(provider.secret_reference)
                if secret and secret.value:
                    return secret.value
        except Exception as exc:
            logger.warning("Failed to fetch KV secret %s: %s", provider.secret_reference, exc)
    # Fallback: check environment variable
    env_key = provider.name.upper().replace(" ", "_") + "_API_KEY"
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    return None


async def build_foundry_client(provider: AIProvider, model: AIModel) -> FoundryClient:
    api_key = await _resolve_api_key(provider)
    use_mi = provider.auth_type == "managed_identity"
    return FoundryClient(
        base_url=provider.base_url,
        deployment_name=model.deployment_name,
        api_key=api_key,
        use_managed_identity=use_mi and not api_key,
    )


KNOWN_CONNECTOR_TYPES = ["odoo", "github", "azure", "microsoft_365", "azure_devops"]

CONNECTOR_DISPLAY_NAMES: dict[str, str] = {
    "odoo": "Odoo",
    "github": "GitHub",
    "azure": "Azure",
    "microsoft_365": "Microsoft 365",
    "azure_devops": "Azure DevOps",
    "slack": "Slack",
    "teams": "Microsoft Teams",
}

TOOL_CONNECTOR_MAP = {
    "odoo": {
        "connector_url_env": "ODOO_CONNECTOR_URL",
        "connector_key_env": "ODOO_CONNECTOR_API_KEY",
    },
}

ODOO_CONNECTOR_URL: str = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY: str = os.environ.get("ODOO_CONNECTOR_API_KEY", "")


async def _get_available_tools(db: AsyncSession, user_id: Optional[UUID]) -> list[AITool]:
    """Get active tools for systems the user has connected accounts for."""
    if not user_id:
        return []
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    accounts = result.scalars().all()
    connected_systems = {a.provider for a in accounts}
    if not connected_systems:
        return []
    result = await db.execute(
        select(AITool).where(
            AITool.status == "active",
            AITool.target_system.in_(connected_systems),
        ).order_by(AITool.name)
    )
    return result.scalars().all()


def _normalize_tool_name(name: str) -> str:
    """Replace invalid chars with underscores, cap at 64 chars."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


TOOL_NAME_MAP: dict[str, str] = {}


def _build_tool_definitions(tools: list[AITool]) -> list[dict[str, Any]]:
    """Convert AITool records to OpenAI-compatible tool definitions.
    Normalizes names to comply with the API's allowed character set.
    """
    global TOOL_NAME_MAP
    definitions = []
    for tool in tools:
        schema = tool.input_schema
        if not schema:
            continue
        normalized = _normalize_tool_name(tool.name)
        TOOL_NAME_MAP[normalized] = tool.name
        if normalized != tool.name:
            logger.info("Normalized tool name '%s' to '%s'", tool.name, normalized)
        definitions.append({
            "type": "function",
            "function": {
                "name": normalized,
                "description": tool.description or "",
                "parameters": schema,
            },
        })
    return definitions


async def _resolve_odoo_credentials_for_tool(db: AsyncSession, user_id: UUID) -> dict[str, str]:
    """Resolve Odoo credentials for a given user (internal tool-execution path)."""
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id),
            AIConnectedAccount.provider == "odoo",
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise RuntimeError("No Odoo connected account found for tool execution")

    api_key = ""
    if account.secret_reference and os.environ.get("KEY_VAULT_URI"):
        try:
            credential = DefaultAzureCredential()
            kv_client = SecretClient(vault_url=os.environ["KEY_VAULT_URI"], credential=credential)
            secret = kv_client.get_secret(account.secret_reference)
            api_key = secret.value or ""
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve Odoo credentials from Key Vault: {e}")

    if not api_key:
        raise RuntimeError("Odoo connected account has no valid credentials")

    # Use the saved Odoo URL/DB from the connected account record.
    # Fall back to company facts or env vars for backwards compatibility.
    odoo_url = account.odoo_url or ""
    odoo_db = account.odoo_db or ""
    if not odoo_url or not odoo_db:
        url_result = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_url"))
        db_result = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_primary_db"))
        url_fact = url_result.scalar_one_or_none()
        db_fact = db_result.scalar_one_or_none()
        odoo_url = url_fact.value if url_fact else os.environ.get("ODOO_URL", "")
        odoo_db = db_fact.value if db_fact else os.environ.get("ODOO_DB", "")

    if not odoo_url or not odoo_db:
        raise RuntimeError("Odoo URL or database not configured")

    logger.info("Resolved Odoo credentials for tool execution: user=%s host=%s db=%s",
                account.provider_username, odoo_url, odoo_db)

    return {
        "url": odoo_url,
        "db": odoo_db,
        "username": account.provider_username or "",
        "api_key": api_key,
        "transport": "auto",
    }


async def _execute_tool_call(
    db: AsyncSession,
    user_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a tool call by routing to the appropriate connector."""
    if tool_name.startswith("odoo_"):
        credentials = await _resolve_odoo_credentials_for_tool(db, user_id)
        path = _map_odoo_tool_to_path(tool_name)
        payload = {
            "credentials": credentials,
            "identity_mode": "user-delegated",
            **arguments,
        }
        url = f"{ODOO_CONNECTOR_URL.rstrip('/')}{path}" if ODOO_CONNECTOR_URL else ""
        if not url:
            return {"error": "Odoo connector URL not configured"}
        headers = {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            return {"error": str(detail), "status_code": response.status_code}
        return response.json()

    return {"error": f"Unknown tool: {tool_name}"}


def _map_odoo_tool_to_path(tool_name: str) -> str:
    mapping = {
        "odoo_search_read": "/records/search-read",
        "odoo_execute_kw": "/execute-kw/",
        "odoo_schema": "/schema/fields",
        "odoo_attachments_list": "/attachments/list",
        "odoo_attachments_get": "/attachments/get",
        "odoo_messages_list": "/messages/list",
        "odoo_messages_create": "/messages/create",
    }
    return mapping.get(tool_name, "")


async def _get_connector_context(db: AsyncSession, user_id: Optional[UUID]) -> str:
    """Build a connector-availability context block for the current user.

    Queries AIConnectedAccount for the user and returns a human-readable
    block that the model can use to know which systems are actually available.
    """
    lines: list[str] = ["Connected Account Status:"]
    if not user_id:
        lines.append("  (no authenticated user context)")
        return "\n".join(lines)

    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
        )
    )
    accounts = result.scalars().all()

    conn_map: dict[str, str] = {}
    for acct in accounts:
        status = acct.status
        if status == "connected":
            conn_map[acct.provider] = "connected"
        elif status == "active":
            conn_map[acct.provider] = "connected"
        else:
            conn_map[acct.provider] = status

    for conn_type in KNOWN_CONNECTOR_TYPES:
        display_name = CONNECTOR_DISPLAY_NAMES.get(conn_type, conn_type.replace("_", " ").title())
        if conn_type in conn_map:
            status = conn_map[conn_type]
            icon = "✓" if status == "connected" else "✗"
            lines.append(f"  {icon} {display_name}: {status}")
        else:
            lines.append(f"  - {display_name}: not connected")

    return "\n".join(lines)


async def execute_chat(
    db: AsyncSession,
    messages: list,
    task_type: str = "general_chat",
    chat_session_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> dict:
    # Use ModelRoutingPolicyService to determine primary route and fallback properties
    from app.services.model_routing_policy import ModelRoutingPolicyService
    from app.core.config import get_settings
    routing_svc = ModelRoutingPolicyService(db)

    # Analyze risk level based on task_type and text content
    user_msg_text = messages[-1]["content"] if messages else ""
    is_finance_topic = any(kw in user_msg_text.lower() for kw in [
        "revenue", "income", "expense", "profit", "loss", "balance", "invoice",
        "bill", "payment", "amount", "total", "cost", "price", "tax", "vat", "accounting"
    ])
    risk_level = "high" if is_finance_topic else "low"

    policy = await routing_svc.select_route(
        task_type=task_type,
        risk_level=risk_level,
        requires_tools=True if task_type == "general_chat" else False
    )

    route_id_str = policy.get("selected_route_id")
    model_id_str = policy.get("selected_model_id")

    if route_id_str and model_id_str:
        # Load selected route
        route_res = await db.execute(select(AIRoute).where(AIRoute.id == UUID(route_id_str)))
        route = route_res.scalar_one_or_none()

        # Load selected model
        model_res = await db.execute(select(AIModel).where(AIModel.id == UUID(model_id_str)))
        model_obj = model_res.scalar_one_or_none()

        # Load provider
        prov_res = await db.execute(select(AIProvider).where(AIProvider.id == model_obj.provider_id))
        provider = prov_res.scalar_one_or_none()
    else:
        # Fallback to legacy get_enabled_route if none found in DB
        route, model_obj, provider = await get_enabled_route(db, task_type)

    # Build system prompt with dynamic connector context
    system_prompt = route.system_prompt or ""
    connector_context = await _get_connector_context(db, user_id)
    if connector_context:
        system_prompt = system_prompt.rstrip() + "\n\n" + connector_context

    # Fetch available tools for connected systems
    tools: list[AITool] = []
    tool_definitions: list[dict[str, Any]] = []
    supports_tools = model_obj.supports_tools == "true"
    if supports_tools:
        tools = await _get_available_tools(db, user_id)
        tool_definitions = _build_tool_definitions(tools)
        if tool_definitions:
            system_prompt += (
                "\n\nYou have access to the following tools. "
                "When the user asks about data from a connected system, call the appropriate tool "
                "rather than saying you cannot access it. "
                "Use tools proactively when relevant."
            )

    # Inject business rules and company facts into the system prompt
    injected_rules: list[Any] = []
    injected_facts: list[Any] = []
    try:
        connected_systems: set[str] = set()
        if user_id:
            acct_result = await db.execute(
                select(AIConnectedAccount).where(
                    AIConnectedAccount.user_id == user_id,
                    or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
                )
            )
            connected_systems = {a.provider for a in acct_result.scalars().all()}
        context_svc = ContextService(db)
        context = await context_svc.get_context(
            ContextRequest(
                task="general_chat",
                systems=list(connected_systems) if connected_systems else None,
                limit=50,
            ),
            user_id=user_id,
        )
        injected_rules = context.get("rules", [])
        injected_facts = context.get("facts", [])
        if injected_rules:
            rules_text = "\n".join(
                f"- [Priority {r.priority}] {r.body}" for r in injected_rules
            )
            system_prompt += f"\n\n## Active Business Rules\n{rules_text}"
        if injected_facts:
            facts_text = "\n".join(
                f"- {f.key}: {f.value}" for f in injected_facts
            )
            system_prompt += f"\n\n## Company Facts\n{facts_text}"
        # Inject Odoo company currency if available
        odoo_currency_str = None
        currency_source = "none"
        if user_id:
            acct_result2 = await db.execute(
                select(AIConnectedAccount).where(
                    AIConnectedAccount.user_id == user_id,
                    AIConnectedAccount.provider == "odoo",
                    or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
                )
            )
            odoo_account = acct_result2.scalars().first()
            if odoo_account and odoo_account.odoo_currency_code:
                code = odoo_account.odoo_currency_code
                symbol = odoo_account.odoo_currency_symbol or code
                company = odoo_account.odoo_company_name or "your company"
                odoo_currency_str = f"{company} uses {code} ({symbol})"
                system_prompt += f"\n\n## Connected Odoo Currency\n{odoo_currency_str}"
                currency_source = "odoo_connected_account"

        # Inject active memories (learned preferences, patterns, resolved cases)
        injected_memories: list[Any] = []
        try:
            if user_id:
                mem_result = await db.execute(
                    select(AIMemory).where(
                        AIMemory.created_by_user_id == user_id,
                        AIMemory.status == "active",
                    )
                    .order_by(AIMemory.priority.asc(), AIMemory.last_used_at.desc().nullslast())
                    .limit(30)
                )
                injected_memories = mem_result.scalars().all()
                if injected_memories:
                    mem_blocks: list[str] = []
                    for m in injected_memories:
                        formatted = f"- [{m.type}] {m.title}"
                        if m.summary:
                            formatted += f": {m.summary}"
                        if m.body:
                            formatted += f"\n  Detail: {m.body[:300]}"
                        mem_blocks.append(formatted)
                    system_prompt += "\n\n## Learned from Past Interactions\n" + "\n".join(mem_blocks)
        except Exception as mem_exc:
            logger.warning("Failed to inject memories: %s", mem_exc)

        # Search Azure AI Search for relevant SOPs, procedures, and long documents
        chunks_to_inject: list[dict[str, Any]] = []
        try:
            from app.services.search_service import SearchService
            search_svc = SearchService()
            if search_svc.enabled and messages:
                user_query = messages[-1]["content"]
                # Query Azure AI Search with filters
                injected_search_results = await search_svc.search_memories(
                    query=user_query,
                    user_id=user_id,
                    status="active"
                )

                # Check feature flag and limit max injected chunks
                from app.core.config import get_settings
                max_chunks = get_settings().azure_search_max_injected_chunks
                chunks_to_inject = injected_search_results[:max_chunks]

                if chunks_to_inject:
                    search_blocks = []
                    for hit in chunks_to_inject:
                        source_type = hit.get("type") or hit.get("source_type") or "reference"
                        title = hit.get("title") or "Untitled Document"
                        chunk_text = hit.get("chunk_text") or hit.get("summary") or ""
                        block = f"- [{source_type}] {title}"
                        if chunk_text:
                            block += f"\n  Details: {chunk_text[:350]}"
                        search_blocks.append(block)

                    system_prompt += "\n\n## Relevant Reference Materials\n" + "\n".join(search_blocks)
        except Exception as search_exc:
            logger.warning("Failed to retrieve or inject search results: %s", search_exc)

        logger.info(
            "Context injected | rules=%d facts=%d memories=%d search_results=%d user_id=%s currency=%s",
            len(injected_rules), len(injected_facts), len(injected_memories), len(chunks_to_inject), user_id, odoo_currency_str or "none",
        )
    except Exception as exc:
        logger.warning("Failed to inject context: %s", exc)

    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
    else:
        full_messages = messages

    temperature = float(route.temperature) if route.temperature is not None else 0.3
    max_tokens = route.max_tokens or 2000

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_latency_ms = 0
    total_tool_calls = 0
    tool_results: list[dict[str, Any]] = []

    async def _try_model(model, prov, msgs):
        cl = await build_foundry_client(prov, model)
        res = await cl.chat_completion(
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
        )
        return res, model, prov, cl

    result, used_model, used_provider, client = await _try_model(model_obj, provider, full_messages)
    total_prompt_tokens += result.get("prompt_tokens", 0)
    total_completion_tokens += result.get("completion_tokens", 0)
    total_latency_ms += result.get("latency_ms", 0)

    # Fallback on quota / rate-limit errors
    fallback_used = False
    primary_model_display = model_obj.display_name
    fallback_model_display = "none"

    if result.get("error") and result.get("error_type") in ("rate_limit_exceeded", "quota_exceeded"):
        if route.fallback_model_id:
            fallback_model_res = await db.execute(
                select(AIModel).where(AIModel.id == route.fallback_model_id, AIModel.enabled == "true")
            )
            fallback_model = fallback_model_res.scalar_one_or_none()
            if fallback_model:
                fallback_model_display = fallback_model.display_name
                # Verify that fallback supports required tools (if tools are enabled/requested)
                config_fb = fallback_model.config_json or {}
                fb_supports_tools = fallback_model.supports_tools == "true" or config_fb.get("supports_tools") is True

                if tool_definitions and not fb_supports_tools:
                    logger.warning("Fallback model %s does not support required tools. Skipping fallback.", fallback_model.display_name)
                else:
                    fallback_prov_res = await db.execute(
                        select(AIProvider).where(AIProvider.id == fallback_model.provider_id, AIProvider.enabled == "true")
                    )
                    fallback_prov = fallback_prov_res.scalar_one_or_none()
                    if fallback_prov:
                        logger.warning(
                            "Primary model quota exceeded, trying fallback | primary=%s fallback=%s",
                            model_obj.display_name, fallback_model.display_name,
                        )
                        fb_result, used_model, used_provider, client = await _try_model(fallback_model, fallback_prov, full_messages)
                        total_prompt_tokens += fb_result.get("prompt_tokens", 0)
                        total_completion_tokens += fb_result.get("completion_tokens", 0)
                        total_latency_ms += fb_result.get("latency_ms", 0)
                        result = fb_result
                        fallback_used = True

    # Tool-calling loop (up to 10 rounds to prevent infinite loops)
    max_tool_rounds = 10
    tool_round = 0
    while tool_round < max_tool_rounds:
        if result.get("error"):
            break

        tool_calls = result.get("tool_calls")
        if not tool_calls or not tools:
            break

        tool_round += 1
        total_tool_calls += len(tool_calls)

        assistant_msg = {"role": "assistant", "content": result.get("content") or None}
        assistant_msg["tool_calls"] = [
            {"id": tc["id"], "type": tc["type"], "function": tc["function"]}
            for tc in tool_calls
        ]
        full_messages.append(assistant_msg)

        for tc in tool_calls:
            if tc.get("type") != "function":
                continue
            func = tc.get("function", {})
            name = func.get("name", "")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}

            tc_result = await _execute_tool_call(db, user_id, name, args)
            tool_results.append({
                "tool_call_id": tc.get("id", ""),
                "tool_name": name,
                "arguments": args,
                "result": tc_result,
            })

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps(tc_result),
            })

        result = await client.chat_completion(
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
        )

        total_prompt_tokens += result.get("prompt_tokens", 0)
        total_completion_tokens += result.get("completion_tokens", 0)
        total_latency_ms += result.get("latency_ms", 0)

    log = AIUsageLog(
        provider_id=used_provider.id,
        model_id=used_model.id,
        route_id=route.id,
        task_type=task_type,
        chat_session_id=chat_session_id,
        user_id=user_id,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_prompt_tokens + total_completion_tokens,
        latency_ms=total_latency_ms,
        status="failed" if result.get("error") else "success",
        error_message=result.get("message") if result.get("error") else None,
    )
    db.add(log)
    await db.flush()

    if result.get("error"):
        error_type = result.get("error_type", "unknown")
        raw_message = result.get("message", "Provider returned an error")
        status_code = result.get("status_code", 0)

        # Structured logging for developer debugging
        logger.error(
            "Provider call failed | provider=%s model=%s deployment=%s "
            "error_type=%s status_code=%s raw_message=%s "
            "user_id=%s chat_session_id=%s tools_enabled=%s",
            used_provider.name,
            used_model.display_name,
            used_model.deployment_name,
            error_type,
            status_code,
            raw_message,
            user_id,
            chat_session_id,
            bool(tool_definitions),
        )

        user_facing = {
            "rate_limit_exceeded": (
                "The AI service is temporarily unavailable because the model "
                "quota or rate limit has been reached. "
                "Please try again shortly, or contact support if this continues."
            ),
            "quota_exceeded": (
                "The AI service is temporarily unavailable because the model "
                "quota or rate limit has been reached. "
                "Please try again shortly, or contact support if this continues."
            ),
            "authentication_error": (
                "The AI service is unavailable due to an authentication issue. "
                "Please contact support."
            ),
            "authorization_error": (
                "The AI service is unavailable due to an authorization issue. "
                "Please contact support."
            ),
            "model_not_found": (
                "The configured AI model could not be found. "
                "Please contact support."
            ),
            "server_error": (
                "The AI service is temporarily unavailable. "
                "Please try again shortly, or contact support if this continues."
            ),
            "bad_request": (
                "The AI service received an invalid request. "
                "Please try again, or contact support if this continues."
            ),
        }.get(error_type, (
            "The AI service is temporarily unavailable. "
            "Please try again shortly, or contact support if this continues."
        ))

        raise ProviderCallError(
            user_facing,
            provider.name,
            model_obj.display_name,
        )

    context_metadata = {
        "rules_injected": [{"id": str(r.id), "title": r.title, "priority": r.priority} for r in injected_rules] if injected_rules else [],
        "facts_injected": [{"key": f.key, "value": f.value} for f in injected_facts] if injected_facts else [],
        "memories_injected": [{"id": str(m.id), "title": m.title, "type": m.type} for m in injected_memories] if injected_memories else [],
        "search_results_injected": [
            {
                "id": hit.get("id"),
                "title": hit.get("title"),
                "type": hit.get("type"),
                "score": hit.get("score")
            }
            for hit in chunks_to_inject
        ] if chunks_to_inject else [],
        "currency_source": currency_source,
        "model_routing": {
            "primary_model": primary_model_display,
            "fallback_model": fallback_model_display,
            "fallback_used": fallback_used,
            "routing_reason": policy.get("reason", "unknown") if "policy" in locals() else "legacy_get_enabled_route",
            "cost_tier": policy.get("cost_tier", "medium") if "policy" in locals() else "medium"
        }
    }

    response = {
        "content": result.get("content", ""),
        "finish_reason": result.get("finish_reason", ""),
        "model_provider": used_provider.name,
        "model_name": used_model.display_name,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "latency_ms": total_latency_ms,
        "tool_calls": tool_results if tool_results else None,
        "context": context_metadata,
        "tool_call_count": total_tool_calls,
    }
    return response
