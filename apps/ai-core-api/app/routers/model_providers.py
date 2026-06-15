import re
import uuid
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIModel, AIProvider, AIRoute, AITrace, AIUsageLog
from app.services.key_vault import delete_secret, get_secret_value, key_vault_uri, set_secret_value
from app.services.model_provider_client import ModelProviderClient
from app.services.model_router import CANONICAL_SYSTEM_PROMPT


router = APIRouter(prefix="/model-providers", tags=["model-providers"])

OPENAI_COMPATIBLE = "openai_compatible"
CHAT_ROUTE = "general_chat"


class ProviderModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    model_name: str
    deployment_name: str
    supports_tools: str
    supports_json_schema: str
    context_window: int | None = None
    enabled: str


class ProviderResponse(BaseModel):
    id: UUID
    name: str
    provider_type: str
    base_url: str
    enabled: str
    api_key_status: str
    secret_reference: str | None = None
    models: list[ProviderModelResponse]


class RouteResponse(BaseModel):
    task_type: str
    primary_model_id: UUID | None = None


class ModelSyncResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    route: RouteResponse | None = None
    sync: ModelSyncResponse | None = None


class ProviderUpsertRequest(BaseModel):
    provider_id: UUID | None = None
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    enabled: bool = True

    @field_validator("name", "base_url", mode="before")
    @classmethod
    def strip_text(_cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class ModelToggleRequest(BaseModel):
    enabled: bool = True


class RouteUpdateRequest(BaseModel):
    primary_model_id: UUID


class ProviderTestRequest(BaseModel):
    provider_id: UUID | None = None
    model_id: UUID | None = None
    name: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None

    @field_validator("name", "base_url", "model_name", mode="before")
    @classmethod
    def strip_optional_text(_cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class ProviderTestResponse(BaseModel):
    success: bool
    message: str
    provider: str | None = None
    model: str | None = None


class DiscoveredModel(BaseModel):
    id: str
    display_name: str | None = None
    context_window: int | None = None
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None


def _is_admin(auth: dict[str, Any]) -> bool:
    roles = {str(role).lower() for role in auth.get("roles") or []}
    return (
        auth.get("mode") in {"test", "api-key"}
        or str(auth.get("db_role") or "").lower() == "admin"
        or "aiplatform.admin" in roles
        or "admin" in roles
    )


def _require_admin(auth: dict[str, Any]) -> None:
    if not _is_admin(auth):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI provider settings require administrator access.",
        )


def _secret_name(provider_name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", provider_name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-") or "provider"
    return f"model-provider-{slug[:94]}-api-key"


def _bool_string(value: bool) -> str:
    return "true" if value else "false"


def _display_name_for_model(model_id: str) -> str:
    clean = model_id.strip()
    if "/" in clean:
        clean = clean.rsplit("/", 1)[-1]
    return clean or model_id


def _chat_model_sort_key(model: AIModel) -> tuple[int, str]:
    name = (model.model_name or model.display_name or "").lower()
    score = 0
    if "auto" in name:
        score += 60
    if "chat" in name:
        score += 30
    if "latest" in name:
        score += 20
    if "instruct" in name:
        score += 10
    if any(marker in name for marker in ("vision", "audio", "embedding", "rerank")):
        score -= 80
    if "code" in name:
        score -= 20

    version_matches = re.findall(r"(?:^|[-_.])(?:v|k)?(\d+(?:\.\d+)*)(?![\dk])", name)
    for raw_version in version_matches:
        try:
            version_value = float(raw_version)
        except ValueError:
            continue
        if version_value < 20:
            score += int(version_value * 10)
    return (-score, name)


def _models_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def _model_context_window(data: dict[str, Any]) -> int | None:
    for key in ("context_length", "context_window", "context_window_tokens", "max_context_length"):
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _supported_parameters(data: dict[str, Any]) -> set[str]:
    raw = data.get("supported_parameters")
    if isinstance(raw, list):
        return {str(item).lower() for item in raw}
    return set()


def _parse_models_payload(body: Any) -> list[DiscoveredModel]:
    raw_models = body.get("data") if isinstance(body, dict) else body
    if not isinstance(raw_models, list):
        return []

    discovered: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in raw_models:
        if isinstance(item, str):
            model_id = item
            display_name = item
            context_window = None
            supports_tools = None
            supports_json_schema = None
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
            if not model_id:
                continue
            display_name = str(item.get("name") or item.get("display_name") or _display_name_for_model(model_id))
            context_window = _model_context_window(item)
            parameters = _supported_parameters(item)
            supports_tools = True if {"tools", "tool_choice"}.intersection(parameters) else None
            supports_json_schema = True if "response_format" in parameters else None
        else:
            continue

        if model_id in seen:
            continue
        seen.add(model_id)
        discovered.append(
            DiscoveredModel(
                id=model_id,
                display_name=display_name,
                context_window=context_window,
                supports_tools=supports_tools,
                supports_json_schema=supports_json_schema,
            )
        )
    return sorted(discovered, key=lambda model: model.id.lower())


async def _fetch_available_models(base_url: str, api_key: str | None = None) -> list[DiscoveredModel]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_models_url(base_url), headers=headers)
    if response.status_code != 200:
        raise RuntimeError(response.text[:500] or f"Provider returned HTTP {response.status_code}.")
    return _parse_models_payload(response.json())


async def _api_key_status(provider: AIProvider) -> str:
    if not provider.secret_reference:
        return "missing"
    if not key_vault_uri():
        return "vault_not_configured"
    try:
        return "saved" if await get_secret_value(provider.secret_reference) else "missing"
    except Exception:
        return "error"


async def _provider_payload(db: AsyncSession, sync: ModelSyncResponse | None = None) -> ProviderListResponse:
    provider_result = await db.execute(
        select(AIProvider).where(AIProvider.provider_type == OPENAI_COMPATIBLE).order_by(AIProvider.name)
    )
    providers = list(provider_result.scalars().all())
    model_result = await db.execute(
        select(AIModel).where(AIModel.provider_id.in_([p.id for p in providers])).order_by(AIModel.display_name)
    ) if providers else None
    models_by_provider: dict[UUID, list[AIModel]] = {}
    if model_result:
        for model in model_result.scalars().all():
            models_by_provider.setdefault(model.provider_id, []).append(model)

    route_result = await db.execute(select(AIRoute).where(AIRoute.task_type == CHAT_ROUTE))
    route = route_result.scalar_one_or_none()
    return ProviderListResponse(
        providers=[
            ProviderResponse(
                id=provider.id,
                name=provider.name,
                provider_type=provider.provider_type,
                base_url=provider.base_url,
                enabled=provider.enabled,
                secret_reference=provider.secret_reference,
                api_key_status=await _api_key_status(provider),
                models=[ProviderModelResponse.model_validate(model) for model in models_by_provider.get(provider.id, [])],
            )
            for provider in providers
        ],
        route=RouteResponse(
            task_type=CHAT_ROUTE,
            primary_model_id=route.primary_model_id if route else None,
        ) if route else None,
        sync=sync,
    )


async def _get_provider(db: AsyncSession, provider_id: UUID) -> AIProvider:
    result = await db.execute(select(AIProvider).where(AIProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found.")
    return provider


async def _get_model(db: AsyncSession, model_id: UUID) -> AIModel:
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")
    return model


async def _stored_api_key(provider: AIProvider) -> str:
    if provider.secret_reference:
        return await get_secret_value(provider.secret_reference)
    return ""


async def _save_provider_secret(provider: AIProvider, api_key: str | None) -> None:
    if not api_key:
        return
    if not key_vault_uri():
        raise HTTPException(status_code=400, detail="Key Vault is not configured.")
    await set_secret_value(provider.secret_reference, api_key)


async def _enabled_chat_models(db: AsyncSession) -> list[AIModel]:
    result = await db.execute(
        select(AIModel)
        .join(AIProvider, AIProvider.id == AIModel.provider_id)
        .where(
            AIModel.enabled == "true",
            AIProvider.enabled == "true",
            AIProvider.provider_type == OPENAI_COMPATIBLE,
        )
        .order_by(AIProvider.name, AIModel.display_name)
    )
    return sorted(result.scalars().all(), key=_chat_model_sort_key)


async def _enabled_chat_models_excluding(db: AsyncSession, excluded_model_ids: set[UUID]) -> list[AIModel]:
    conditions = [
        AIModel.enabled == "true",
        AIProvider.enabled == "true",
        AIProvider.provider_type == OPENAI_COMPATIBLE,
    ]
    if excluded_model_ids:
        conditions.append(~AIModel.id.in_(excluded_model_ids))
    result = await db.execute(
        select(AIModel)
        .join(AIProvider, AIProvider.id == AIModel.provider_id)
        .where(*conditions)
        .order_by(AIProvider.name, AIModel.display_name)
    )
    return sorted(result.scalars().all(), key=_chat_model_sort_key)


async def _reconcile_chat_route(db: AsyncSession) -> None:
    await db.flush()
    models = await _enabled_chat_models(db)
    route_result = await db.execute(select(AIRoute).where(AIRoute.task_type == CHAT_ROUTE))
    route = route_result.scalar_one_or_none()

    if not models:
        if route:
            route.enabled = "false"
            route.fallback_model_id = None
        return

    enabled_ids = {model.id for model in models}
    primary = route.primary_model_id if route and route.primary_model_id in enabled_ids else models[0].id

    if not route:
        route = AIRoute(
            id=uuid.uuid4(),
            task_type=CHAT_ROUTE,
            primary_model_id=primary,
            temperature=0.3,
            max_tokens=2000,
            system_prompt=CANONICAL_SYSTEM_PROMPT,
            enabled="true",
        )
        db.add(route)

    route.primary_model_id = primary
    route.fallback_model_id = None
    route.enabled = "true"


async def _reconcile_routes_before_model_delete(db: AsyncSession, deleted_model_ids: set[UUID]) -> None:
    if not deleted_model_ids:
        return

    route_result = await db.execute(
        select(AIRoute).where(
            or_(
                AIRoute.primary_model_id.in_(deleted_model_ids),
                AIRoute.fallback_model_id.in_(deleted_model_ids),
            )
        )
    )
    routes = list(route_result.scalars().all())
    if not routes:
        return

    remaining_models = await _enabled_chat_models_excluding(db, deleted_model_ids)
    if not remaining_models:
        route_ids = [route.id for route in routes]
        await db.execute(update(AIUsageLog).where(AIUsageLog.route_id.in_(route_ids)).values(route_id=None))
        await db.execute(update(AITrace).where(AITrace.route_id.in_(route_ids)).values(route_id=None))
        for route in routes:
            await db.delete(route)
        await db.flush()
        return

    remaining_ids = {model.id for model in remaining_models}
    for route in routes:
        if route.primary_model_id in remaining_ids:
            primary = route.primary_model_id
        elif route.fallback_model_id in remaining_ids:
            primary = route.fallback_model_id
        else:
            primary = remaining_models[0].id

        route.primary_model_id = primary
        route.fallback_model_id = None
        route.enabled = "true"
    await db.flush()


async def _sync_provider_models(db: AsyncSession, provider: AIProvider, api_key: str) -> int:
    discovered_models = await _fetch_available_models(provider.base_url, api_key)
    if not discovered_models:
        raise RuntimeError("No models were returned by this provider.")

    existing_result = await db.execute(select(AIModel).where(AIModel.provider_id == provider.id))
    existing_by_name = {model.model_name: model for model in existing_result.scalars().all()}

    for discovered in discovered_models:
        model = existing_by_name.get(discovered.id)
        display_name = discovered.display_name or _display_name_for_model(discovered.id)
        supports_tools = discovered.supports_tools if discovered.supports_tools is not None else True
        supports_json_schema = discovered.supports_json_schema if discovered.supports_json_schema is not None else False

        if not model:
            model = AIModel(
                id=uuid.uuid4(),
                provider_id=provider.id,
                display_name=display_name,
                model_name=discovered.id,
                deployment_name=discovered.id,
                model_family=provider.name,
                model_version=discovered.id,
                enabled="true",
                config_json={},
            )
            db.add(model)

        model.provider_id = provider.id
        model.display_name = display_name
        model.model_name = discovered.id
        model.deployment_name = discovered.id
        model.model_family = provider.name
        model.model_version = discovered.id
        model.supports_tools = _bool_string(supports_tools)
        model.supports_json_schema = _bool_string(supports_json_schema)
        model.context_window = discovered.context_window
        model.config_json = model.config_json if isinstance(model.config_json, dict) else {}

    return len(discovered_models)


@router.get("", response_model=ProviderListResponse)
async def list_model_providers(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    return await _provider_payload(db)


@router.post("", response_model=ProviderListResponse, status_code=status.HTTP_201_CREATED)
async def upsert_model_provider(
    req: ProviderUpsertRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)

    provider = await _get_provider(db, req.provider_id) if req.provider_id else None
    if not provider:
        existing = await db.execute(select(AIProvider).where(AIProvider.name == req.name))
        provider = existing.scalar_one_or_none()

    if not provider:
        provider = AIProvider(
            id=uuid.uuid4(),
            name=req.name,
            provider_type=OPENAI_COMPATIBLE,
            base_url=req.base_url.rstrip("/"),
            auth_type="key_vault_secret",
            secret_reference=_secret_name(req.name),
            enabled=_bool_string(req.enabled),
            capabilities={},
        )
        db.add(provider)
    else:
        provider.name = req.name
        provider.provider_type = OPENAI_COMPATIBLE
        provider.base_url = req.base_url.rstrip("/")
        provider.auth_type = "key_vault_secret"
        provider.enabled = _bool_string(req.enabled)
        if not provider.secret_reference:
            provider.secret_reference = _secret_name(req.name)

    await _save_provider_secret(provider, req.api_key)
    await db.flush()

    sync_result: ModelSyncResponse | None = None
    api_key = req.api_key or await _stored_api_key(provider)
    if provider.enabled == "true" and api_key:
        try:
            model_count = await _sync_provider_models(db, provider, api_key)
            sync_result = ModelSyncResponse(
                success=True,
                message=f"Synced {model_count} models.",
                model_count=model_count,
            )
        except Exception as exc:
            sync_result = ModelSyncResponse(
                success=False,
                message=f"Provider saved, but model sync failed: {exc}",
            )
    elif provider.enabled == "true":
        sync_result = ModelSyncResponse(
            success=False,
            message="Provider saved. Add an API key to sync models.",
        )

    await _reconcile_chat_route(db)
    await db.commit()
    return await _provider_payload(db, sync_result)


@router.post("/discover", include_in_schema=False)
async def removed_model_discovery_route():
    raise HTTPException(status_code=404, detail="Not Found")


@router.delete("/{provider_id}", response_model=ProviderListResponse)
async def delete_model_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    provider_result = await db.execute(select(AIProvider).where(AIProvider.id == provider_id))
    provider = provider_result.scalar_one_or_none()
    if not provider:
        return await _provider_payload(db)

    model_result = await db.execute(select(AIModel).where(AIModel.provider_id == provider.id))
    model_ids = {model.id for model in model_result.scalars().all()}

    await _reconcile_routes_before_model_delete(db, model_ids)
    if model_ids:
        await db.execute(update(AIUsageLog).where(AIUsageLog.model_id.in_(model_ids)).values(model_id=None))
    await db.execute(update(AIUsageLog).where(AIUsageLog.provider_id == provider.id).values(provider_id=None))

    if provider.secret_reference:
        try:
            await delete_secret(provider.secret_reference)
        except Exception:
            pass

    await db.execute(delete(AIModel).where(AIModel.provider_id == provider.id))
    await db.delete(provider)
    await db.commit()
    return await _provider_payload(db)


@router.patch("/{provider_id}/models/{model_id}", response_model=ProviderListResponse)
async def toggle_provider_model(
    provider_id: UUID,
    model_id: UUID,
    req: ModelToggleRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    provider = await _get_provider(db, provider_id)
    model = await _get_model(db, model_id)
    if model.provider_id != provider.id:
        raise HTTPException(status_code=400, detail="Model does not belong to this provider.")
    model.enabled = _bool_string(req.enabled)
    await _reconcile_chat_route(db)
    await db.commit()
    return await _provider_payload(db)


@router.patch("/route", response_model=ProviderListResponse)
async def update_chat_route(
    req: RouteUpdateRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    primary = await _get_model(db, req.primary_model_id)
    await _get_provider(db, primary.provider_id)

    result = await db.execute(select(AIRoute).where(AIRoute.task_type == CHAT_ROUTE))
    route = result.scalar_one_or_none()
    if not route:
        route = AIRoute(
            id=uuid.uuid4(),
            task_type=CHAT_ROUTE,
            primary_model_id=primary.id,
            temperature=0.3,
            max_tokens=2000,
            system_prompt=CANONICAL_SYSTEM_PROMPT,
            enabled="true",
        )
        db.add(route)
    route.primary_model_id = primary.id
    route.fallback_model_id = None
    route.enabled = "true"
    await db.commit()
    return await _provider_payload(db)


@router.post("/test", response_model=ProviderTestResponse)
async def test_model_provider(
    req: ProviderTestRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    provider_name = req.name or "Provider"
    base_url = (req.base_url or "").rstrip("/")
    model_name = req.model_name or ""
    api_key = req.api_key or ""

    if req.provider_id:
        provider = await _get_provider(db, req.provider_id)
        provider_name = provider.name
        base_url = provider.base_url
        if not api_key:
            api_key = await _stored_api_key(provider)
    if req.model_id:
        model = await _get_model(db, req.model_id)
        model_name = model.deployment_name

    if not base_url or not model_name:
        raise HTTPException(status_code=400, detail="Provider endpoint and model are required.")
    if not api_key:
        return ProviderTestResponse(success=False, message="API key is missing.", provider=provider_name, model=model_name)

    client = ModelProviderClient(base_url=base_url, deployment_name=model_name, api_key=api_key)
    try:
        result = await client.chat_completion(
            messages=[{"role": "user", "content": "Reply with only OK."}],
            temperature=0,
            max_tokens=8,
        )
    except Exception as exc:
        return ProviderTestResponse(success=False, message=str(exc), provider=provider_name, model=model_name)
    if result.get("error"):
        return ProviderTestResponse(
            success=False,
            message=str(result.get("message") or result.get("error_type") or "Provider test failed."),
            provider=provider_name,
            model=model_name,
        )
    return ProviderTestResponse(success=True, message="Connection succeeded.", provider=provider_name, model=model_name)
