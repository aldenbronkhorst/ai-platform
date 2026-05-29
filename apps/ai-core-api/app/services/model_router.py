import logging
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog
from app.services.foundry_client import FoundryClient

logger = logging.getLogger(__name__)

ROUTE_NOT_CONFIGURED_MESSAGE = "AI chat is not configured yet. Please ask an administrator to configure a model in Settings \u2192 AI Configuration."


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


def build_foundry_client(provider: AIProvider, model: AIModel) -> FoundryClient:
    return FoundryClient(
        base_url=provider.base_url,
        deployment_name=model.deployment_name,
        api_key=None,
        use_managed_identity=provider.auth_type == "managed_identity",
    )


async def execute_chat(
    db: AsyncSession,
    messages: list,
    task_type: str = "general_chat",
    chat_session_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> dict:
    route, model, provider = await get_enabled_route(db, task_type)

    client = build_foundry_client(provider, model)

    system_prompt = route.system_prompt
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
