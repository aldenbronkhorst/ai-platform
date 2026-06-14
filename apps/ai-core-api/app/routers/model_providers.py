import re
import uuid
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIModel, AIProvider, AIRoute
from app.services.key_vault import get_secret_value, key_vault_uri, set_secret_value
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
    fallback_model_id: UUID | None = None


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    route: RouteResponse | None = None


class ProviderUpsertRequest(BaseModel):
    provider_id: UUID | None = None
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    enabled: bool = True

    @field_validator("name", "base_url", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class ModelUpsertRequest(BaseModel):
    model_id: UUID | None = None
    model_name: str = Field(..., min_length=1, max_length=255)
    display_name: str | None = Field(None, max_length=255)
    enabled: bool = True
    supports_tools: bool = True
    supports_json_schema: bool = False
    context_window: int | None = Field(None, ge=1, le=10_000_000)

    @field_validator("model_name", "display_name", mode="before")
    @classmethod
    def strip_model_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class RouteUpdateRequest(BaseModel):
    primary_model_id: UUID
    fallback_model_id: UUID | None = None


class ProviderTestRequest(BaseModel):
    provider_id: UUID | None = None
    model_id: UUID | None = None
    name: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None

    @field_validator("name", "base_url", "model_name", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
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


class ModelDiscoveryRequest(BaseModel):
    provider_id: UUID | None = None
    base_url: str | None = None
    api_key: str | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def strip_base_url(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class ModelDiscoveryResponse(BaseModel):
    success: bool
    message: str
    models: list[DiscoveredModel] = []


def _is_admin(auth: dict[str, Any]) -> bool:
    roles = {str(role).lower() for role in auth.get("roles") or []}
    return (
        auth.get("mode") == "test"
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


async def _provider_payload(db: AsyncSession) -> ProviderListResponse:
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
            fallback_model_id=getattr(route, "fallback_model_id", None) if route else None,
        ) if route else None,
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


async def _upsert_model(db: AsyncSession, provider: AIProvider, req: ModelUpsertRequest) -> AIModel:
    model = await _get_model(db, req.model_id) if req.model_id else None
    if model and model.provider_id != provider.id:
        raise HTTPException(status_code=400, detail="Model does not belong to this provider.")
    if not model:
        existing_model = await db.execute(
            select(AIModel).where(AIModel.provider_id == provider.id, AIModel.model_name == req.model_name)
        )
        model = existing_model.scalar_one_or_none()

    display_name = req.display_name or _display_name_for_model(req.model_name)
    if not model:
        model = AIModel(
            id=uuid.uuid4(),
            provider_id=provider.id,
            display_name=display_name,
            model_name=req.model_name,
            deployment_name=req.model_name,
            model_family=provider.name,
            model_version=req.model_name,
            enabled=_bool_string(req.enabled),
            config_json={},
        )
        db.add(model)

    model.provider_id = provider.id
    model.display_name = display_name
    model.model_name = req.model_name
    model.deployment_name = req.model_name
    model.model_family = provider.name
    model.model_version = req.model_name
    model.supports_tools = _bool_string(req.supports_tools)
    model.supports_json_schema = _bool_string(req.supports_json_schema)
    model.context_window = req.context_window
    model.enabled = _bool_string(req.enabled)
    if not isinstance(model.config_json, dict):
        model.config_json = {}
    return model


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

    await db.commit()
    return await _provider_payload(db)


@router.post("/discover", response_model=ModelDiscoveryResponse)
async def discover_model_provider_models(
    req: ModelDiscoveryRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    base_url = (req.base_url or "").rstrip("/")
    api_key = req.api_key or ""

    if req.provider_id:
        provider = await _get_provider(db, req.provider_id)
        base_url = provider.base_url
        api_key = api_key or await _stored_api_key(provider)

    if not base_url:
        raise HTTPException(status_code=400, detail="Provider endpoint is required.")

    try:
        models = await _fetch_available_models(base_url, api_key or None)
    except Exception as exc:
        return ModelDiscoveryResponse(success=False, message=str(exc), models=[])
    if not models:
        return ModelDiscoveryResponse(success=False, message="No models were returned by this provider.", models=[])
    return ModelDiscoveryResponse(success=True, message=f"Found {len(models)} models.", models=models)


@router.post("/{provider_id}/models", response_model=ProviderListResponse)
async def upsert_provider_model(
    provider_id: UUID,
    req: ModelUpsertRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    provider = await _get_provider(db, provider_id)
    await _upsert_model(db, provider, req)
    await db.commit()
    return await _provider_payload(db)


@router.patch("/route", response_model=ProviderListResponse)
async def update_chat_route(
    req: RouteUpdateRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    if req.fallback_model_id and req.fallback_model_id == req.primary_model_id:
        raise HTTPException(status_code=400, detail="Primary and fallback must be different models.")

    primary = await _get_model(db, req.primary_model_id)
    fallback = await _get_model(db, req.fallback_model_id) if req.fallback_model_id else None
    await _get_provider(db, primary.provider_id)
    if fallback:
        await _get_provider(db, fallback.provider_id)

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
    route.fallback_model_id = fallback.id if fallback else None
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
