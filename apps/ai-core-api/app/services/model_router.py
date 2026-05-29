import os
import logging
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount
from app.services.foundry_client import FoundryClient

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
        display_name = conn_type.replace("_", " ").title()
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
    route, model, provider = await get_enabled_route(db, task_type)

    client = await build_foundry_client(provider, model)

    # Build system prompt with dynamic connector context
    system_prompt = route.system_prompt or ""
    connector_context = await _get_connector_context(db, user_id)
    if connector_context:
        system_prompt = system_prompt.rstrip() + "\n\n" + connector_context

    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
    else:
        full_messages = messages

    temperature = float(route.temperature) if route.temperature is not None else 0.3
    max_tokens = route.max_tokens or 2000

    result = await client.chat_completion(
        messages=full_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    log = AIUsageLog(
        provider_id=provider.id,
        model_id=model.id,
        route_id=route.id,
        task_type=task_type,
        chat_session_id=chat_session_id,
        user_id=user_id,
        prompt_tokens=result.get("prompt_tokens", 0),
        completion_tokens=result.get("completion_tokens", 0),
        total_tokens=result.get("total_tokens", 0),
        latency_ms=result.get("latency_ms", 0),
        status="failed" if result.get("error") else "success",
        error_message=result.get("message") if result.get("error") else None,
    )
    db.add(log)
    await db.flush()

    if result.get("error"):
        raise ProviderCallError(
            result.get("message", "Provider returned an error"),
            provider.name,
            model.display_name,
        )

    return {
        "content": result["content"],
        "finish_reason": result.get("finish_reason", ""),
        "model_provider": provider.name,
        "model_name": model.display_name,
        "prompt_tokens": result.get("prompt_tokens", 0),
        "completion_tokens": result.get("completion_tokens", 0),
        "total_tokens": result.get("total_tokens", 0),
        "latency_ms": result.get("latency_ms", 0),
    }
