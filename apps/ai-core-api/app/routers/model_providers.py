import re
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.models.models import AIModel, AIProvider, AIRoute, AITrace, AIUsageLog
from app.services.key_vault import delete_secret, get_secret_value, key_vault_uri, set_secret_value
from app.services.model_router import CANONICAL_SYSTEM_PROMPT


router = APIRouter(prefix="/model-providers", tags=["model-providers"])

OPENAI_COMPATIBLE = "openai_compatible"
ELEVENLABS = "elevenlabs"
SUPPORTED_PROVIDER_TYPES = {OPENAI_COMPATIBLE, ELEVENLABS}
CHAT_ROUTE = "general_chat"
CHAT_MODEL_TASK = "chat"
VOICE_TRANSCRIPTION_MODEL_TASK = "voice_transcription"
IMAGE_GENERATION_MODEL_TASK = "image_generation"
ZAI_TRANSCRIPTION_MODEL = "glm-asr-2512"
ELEVENLABS_SCRIBE_V2_MODEL = "scribe_v2"
CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY = "custom_openai_compatible"

MODEL_TASK_LABELS = {
    CHAT_MODEL_TASK: "Chat",
    VOICE_TRANSCRIPTION_MODEL_TASK: "Voice",
    IMAGE_GENERATION_MODEL_TASK: "Image",
}

ROUTE_DEFINITIONS = {
    CHAT_ROUTE: {
        "label": "Chat",
        "model_task_type": CHAT_MODEL_TASK,
    },
    VOICE_TRANSCRIPTION_MODEL_TASK: {
        "label": "Voice transcription",
        "model_task_type": VOICE_TRANSCRIPTION_MODEL_TASK,
    },
    IMAGE_GENERATION_MODEL_TASK: {
        "label": "Image generation",
        "model_task_type": IMAGE_GENERATION_MODEL_TASK,
    },
}


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
    config_json: dict[str, Any] | None = None


class ProviderResponse(BaseModel):
    id: UUID
    name: str
    provider_key: str | None = None
    provider_type: str
    base_url: str
    enabled: str
    api_key_status: str
    secret_reference: str | None = None
    models: list[ProviderModelResponse]


class RouteResponse(BaseModel):
    task_type: str
    label: str | None = None
    model_task_type: str | None = None
    primary_model_id: UUID | None = None


class ProviderCatalogResponse(BaseModel):
    key: str
    name: str
    provider_type: str
    base_url: str
    auth_label: str
    supports_custom_name: bool = False
    supports_custom_base_url: bool = False
    configured_provider_id: UUID | None = None


class ModelSyncResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    route: RouteResponse | None = None
    routes: list[RouteResponse] = Field(default_factory=list)
    catalog: list[ProviderCatalogResponse] = Field(default_factory=list)
    sync: ModelSyncResponse | None = None


class ProviderUpsertRequest(BaseModel):
    provider_id: UUID | None = None
    provider_key: str | None = Field(None, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    provider_type: str | None = Field(None, max_length=50)
    api_key: str | None = Field(None, max_length=4000)
    enabled: bool = True

    @field_validator("name", "base_url", mode="before")
    @classmethod
    def strip_text(_cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class ModelToggleRequest(BaseModel):
    enabled: bool | None = None
    task_type: str | None = None


class RouteUpdateRequest(BaseModel):
    primary_model_id: UUID
    task_type: str = CHAT_ROUTE


class DiscoveredModel(BaseModel):
    id: str
    display_name: str | None = None
    context_window: int | None = None
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None
    task_type: str = CHAT_MODEL_TASK


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    name: str
    provider_type: str
    base_url: str
    auth_label: str = "API key"
    supports_custom_name: bool = False
    supports_custom_base_url: bool = False
    catalog_models: tuple[DiscoveredModel, ...] = ()


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="openai",
        name="OpenAI",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
    ),
    ProviderPreset(
        key="google_gemini",
        name="Google Gemini",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    ProviderPreset(
        key="zai",
        name="Z.ai / GLM",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.z.ai/api/paas/v4",
        catalog_models=(
            DiscoveredModel(
                id=ZAI_TRANSCRIPTION_MODEL,
                display_name="GLM ASR 2512",
                supports_tools=False,
                supports_json_schema=False,
                task_type=VOICE_TRANSCRIPTION_MODEL_TASK,
            ),
        ),
    ),
    ProviderPreset(
        key="moonshot",
        name="Moonshot / Kimi",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.moonshot.ai/v1",
    ),
    ProviderPreset(
        key="mistral",
        name="Mistral AI",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.mistral.ai/v1",
    ),
    ProviderPreset(
        key="groq",
        name="Groq",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.groq.com/openai/v1",
    ),
    ProviderPreset(
        key="together",
        name="Together AI",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.together.xyz/v1",
    ),
    ProviderPreset(
        key="fireworks",
        name="Fireworks AI",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.fireworks.ai/inference/v1",
    ),
    ProviderPreset(
        key="openrouter",
        name="OpenRouter",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://openrouter.ai/api/v1",
    ),
    ProviderPreset(
        key="deepseek",
        name="DeepSeek",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.deepseek.com/v1",
    ),
    ProviderPreset(
        key="xai",
        name="xAI",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.x.ai/v1",
    ),
    ProviderPreset(
        key="nvidia",
        name="NVIDIA NIM",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://integrate.api.nvidia.com/v1",
    ),
    ProviderPreset(
        key="perplexity",
        name="Perplexity",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.perplexity.ai",
    ),
    ProviderPreset(
        key="cohere",
        name="Cohere",
        provider_type=OPENAI_COMPATIBLE,
        base_url="https://api.cohere.ai/compatibility/v1",
    ),
    ProviderPreset(
        key="elevenlabs",
        name="ElevenLabs",
        provider_type=ELEVENLABS,
        base_url="https://api.elevenlabs.io/v1",
        catalog_models=(
            DiscoveredModel(
                id=ELEVENLABS_SCRIBE_V2_MODEL,
                display_name="Scribe v2",
                supports_tools=False,
                supports_json_schema=False,
                task_type=VOICE_TRANSCRIPTION_MODEL_TASK,
            ),
        ),
    ),
)

PROVIDER_PRESETS_BY_KEY = {preset.key: preset for preset in PROVIDER_PRESETS}


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


def _model_task_type(model: AIModel) -> str:
    config = model.config_json if isinstance(model.config_json, dict) else {}
    task_type = str(config.get("task_type") or CHAT_MODEL_TASK).strip()
    return task_type or CHAT_MODEL_TASK


def _is_chat_model(model: AIModel) -> bool:
    return _model_task_type(model) == CHAT_MODEL_TASK


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


def _base_hostname(base_url: str) -> str:
    try:
        return (urlparse(base_url).hostname or "").lower()
    except Exception:
        return ""


def _provider_type_from_base_url(base_url: str) -> str:
    hostname = _base_hostname(base_url)
    if hostname == "api.elevenlabs.io" or hostname.endswith(".elevenlabs.io"):
        return ELEVENLABS
    return OPENAI_COMPATIBLE


def _normalize_provider_type(provider_type: str | None, base_url: str) -> str:
    raw = (provider_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return _provider_type_from_base_url(base_url)
    aliases = {
        "openai": OPENAI_COMPATIBLE,
        "openai_compatible": OPENAI_COMPATIBLE,
        "openai-compatible": OPENAI_COMPATIBLE,
        "compatible": OPENAI_COMPATIBLE,
        "eleven": ELEVENLABS,
        "eleven_labs": ELEVENLABS,
        "elevenlabs": ELEVENLABS,
    }
    normalized = aliases.get(raw, raw)
    if normalized not in SUPPORTED_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported provider type: {provider_type}.")
    return normalized


def _normalize_provider_key(provider_key: str | None) -> str | None:
    if not provider_key:
        return None
    key = provider_key.strip().lower().replace("-", "_").replace(" ", "_")
    return key or None


def _provider_capabilities(provider: AIProvider) -> dict[str, Any]:
    return dict(provider.capabilities) if isinstance(provider.capabilities, dict) else {}


def _preset_for_base_url(base_url: str, provider_type: str | None = None) -> ProviderPreset | None:
    normalized_base_url = base_url.rstrip("/")
    for preset in PROVIDER_PRESETS:
        if preset.base_url.rstrip("/") == normalized_base_url:
            return preset
    hostname = _base_hostname(base_url)
    for preset in PROVIDER_PRESETS:
        if provider_type and preset.provider_type != provider_type:
            continue
        if _base_hostname(preset.base_url) == hostname:
            return preset
    return None


def _provider_preset(provider: AIProvider) -> ProviderPreset | None:
    key = _normalize_provider_key(_provider_capabilities(provider).get("provider_key"))
    if key and key in PROVIDER_PRESETS_BY_KEY:
        return PROVIDER_PRESETS_BY_KEY[key]
    return _preset_for_base_url(provider.base_url, provider.provider_type)


def _provider_key(provider: AIProvider) -> str | None:
    preset = _provider_preset(provider)
    return preset.key if preset else None


def _provider_catalog(providers: list[AIProvider]) -> list[ProviderCatalogResponse]:
    configured_by_key: dict[str, UUID] = {}
    for provider in providers:
        key = _provider_key(provider)
        if key and key not in configured_by_key:
            configured_by_key[key] = provider.id
    return [
        ProviderCatalogResponse(
            key=preset.key,
            name=preset.name,
            provider_type=preset.provider_type,
            base_url=preset.base_url,
            auth_label=preset.auth_label,
            supports_custom_name=preset.supports_custom_name,
            supports_custom_base_url=preset.supports_custom_base_url,
            configured_provider_id=configured_by_key.get(preset.key),
        )
        for preset in PROVIDER_PRESETS
    ]


def _resolve_provider_request(req: ProviderUpsertRequest) -> tuple[str, str, str, str | None]:
    provider_key = _normalize_provider_key(req.provider_key)
    if provider_key and provider_key != CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY:
        preset = PROVIDER_PRESETS_BY_KEY.get(provider_key)
        if not preset:
            raise HTTPException(status_code=400, detail=f"Unsupported provider preset: {req.provider_key}.")
        name = req.name.strip() if preset.supports_custom_name else preset.name
        base_url = req.base_url.strip() if preset.supports_custom_base_url else preset.base_url
        return name, base_url.rstrip("/"), preset.provider_type, preset.key

    name = req.name.strip()
    base_url = req.base_url.strip().rstrip("/")
    if not name:
        raise HTTPException(status_code=400, detail="Provider name is required.")
    if not base_url:
        raise HTTPException(status_code=400, detail="Provider API endpoint is required.")
    provider_type = _normalize_provider_type(req.provider_type, base_url)
    preset = _preset_for_base_url(base_url, provider_type)
    return name, base_url, provider_type, preset.key if preset else None


def _route_definition(task_type: str) -> dict[str, str]:
    definition = ROUTE_DEFINITIONS.get(task_type)
    if not definition:
        raise HTTPException(status_code=400, detail=f"Unsupported model route: {task_type}.")
    return definition


def _route_model_task_type(task_type: str) -> str:
    return _route_definition(task_type)["model_task_type"]


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


def _lower_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.lower()}
    if isinstance(value, list):
        return {str(item).lower() for item in value if item is not None}
    if isinstance(value, dict):
        strings: set[str] = set()
        for key, item in value.items():
            if isinstance(item, bool) and item:
                strings.add(str(key).lower())
            else:
                strings.update(_lower_strings(item))
        return strings
    return set()


VOICE_TRANSCRIPTION_METADATA = {
    "automatic_speech_recognition",
    "asr",
    "speech_to_text",
    "speech-to-text",
    "stt",
    "transcribe",
    "transcription",
    "voice_transcription",
    "voice-transcription",
}

IMAGE_GENERATION_METADATA = {
    "image",
    "image_generation",
    "image-generation",
    "text_to_image",
    "text-to-image",
    "vision_generation",
}

VOICE_MODEL_ID_MARKERS = (
    "asr",
    "audio-transcription",
    "audio_transcription",
    "scribe",
    "speech-to-text",
    "speech_to_text",
    "transcribe",
    "transcription",
    "whisper",
)

IMAGE_MODEL_ID_MARKERS = (
    "dall-e",
    "dalle",
    "flux",
    "gpt-image",
    "image-generation",
    "image_generation",
    "imagen",
    "stable-diffusion",
    "stable_diffusion",
    "text-to-image",
    "text_to_image",
)


def _is_voice_transcription_metadata(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return (
        normalized in VOICE_TRANSCRIPTION_METADATA
        or "speech_to_text" in normalized
        or "transcription" in normalized
    )


def _is_image_generation_metadata(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return (
        normalized in IMAGE_GENERATION_METADATA
        or "image_generation" in normalized
        or "text_to_image" in normalized
    )


def _task_type_from_model_metadata(model_id: str, data: dict[str, Any]) -> str:
    normalized_model_id = model_id.strip().lower().replace(" ", "_")
    if any(marker in normalized_model_id for marker in VOICE_MODEL_ID_MARKERS):
        return VOICE_TRANSCRIPTION_MODEL_TASK
    if any(marker in normalized_model_id for marker in IMAGE_MODEL_ID_MARKERS):
        return IMAGE_GENERATION_MODEL_TASK

    metadata_values: set[str] = set()
    for key in (
        "type",
        "task",
        "task_type",
        "category",
        "mode",
        "modality",
        "modalities",
        "capability",
        "capabilities",
        "input_modalities",
        "output_modalities",
        "supported_modalities",
    ):
        metadata_values.update(_lower_strings(data.get(key)))

    architecture = data.get("architecture")
    if isinstance(architecture, dict):
        for key in ("modality", "modalities", "input_modalities", "output_modalities"):
            metadata_values.update(_lower_strings(architecture.get(key)))

    if any(_is_voice_transcription_metadata(value) for value in metadata_values):
        return VOICE_TRANSCRIPTION_MODEL_TASK
    if any(_is_image_generation_metadata(value) for value in metadata_values):
        return IMAGE_GENERATION_MODEL_TASK

    return CHAT_MODEL_TASK


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
            task_type = _task_type_from_model_metadata(model_id, {})
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
            if not model_id:
                continue
            display_name = str(item.get("name") or item.get("display_name") or _display_name_for_model(model_id))
            context_window = _model_context_window(item)
            parameters = _supported_parameters(item)
            supports_tools = True if {"tools", "tool_choice"}.intersection(parameters) else None
            supports_json_schema = True if "response_format" in parameters else None
            task_type = _task_type_from_model_metadata(model_id, item)
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
                task_type=task_type,
            )
        )
    return sorted(discovered, key=lambda model: model.id.lower())


def _provider_catalog_models(
    base_url: str,
    provider_type: str = OPENAI_COMPATIBLE,
    provider_key: str | None = None,
) -> list[DiscoveredModel]:
    preset = PROVIDER_PRESETS_BY_KEY.get(_normalize_provider_key(provider_key) or "") or _preset_for_base_url(
        base_url,
        provider_type,
    )
    if preset:
        return list(preset.catalog_models)
    return []


def _merge_catalog_models(
    base_url: str,
    discovered: list[DiscoveredModel],
    provider_type: str = OPENAI_COMPATIBLE,
    provider_key: str | None = None,
) -> list[DiscoveredModel]:
    models_by_id = {model.id: model for model in discovered}
    for model in _provider_catalog_models(base_url, provider_type, provider_key):
        models_by_id.setdefault(model.id, model)
    return sorted(models_by_id.values(), key=lambda model: model.id.lower())


async def _fetch_available_models(
    base_url: str,
    api_key: str | None = None,
    provider_type: str = OPENAI_COMPATIBLE,
    provider_key: str | None = None,
) -> list[DiscoveredModel]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_models_url(base_url), headers=headers)
    if response.status_code != 200:
        raise RuntimeError(response.text[:500] or f"Provider returned HTTP {response.status_code}.")
    return _merge_catalog_models(base_url, _parse_models_payload(response.json()), provider_type, provider_key)


async def _upsert_discovered_model(db: AsyncSession, provider: AIProvider, discovered: DiscoveredModel) -> None:
    result = await db.execute(
        select(AIModel).where(AIModel.provider_id == provider.id, AIModel.model_name == discovered.id)
    )
    model = result.scalar_one_or_none()
    display_name = discovered.display_name or _display_name_for_model(discovered.id)
    supports_tools = discovered.supports_tools if discovered.supports_tools is not None else True
    supports_json_schema = discovered.supports_json_schema if discovered.supports_json_schema is not None else False
    if discovered.task_type != CHAT_MODEL_TASK:
        supports_tools = False
        supports_json_schema = False

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
            config_json={"task_type": discovered.task_type},
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
    config = dict(model.config_json) if isinstance(model.config_json, dict) else {}
    config["task_type"] = discovered.task_type
    model.config_json = config


async def _ensure_provider_catalog_models(db: AsyncSession, providers: list[AIProvider]) -> None:
    for provider in providers:
        for model in _provider_catalog_models(provider.base_url, provider.provider_type, _provider_key(provider)):
            await _upsert_discovered_model(db, provider, model)
    await db.flush()


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
        select(AIProvider)
        .where(AIProvider.provider_type.in_(SUPPORTED_PROVIDER_TYPES))
        .order_by(AIProvider.name)
    )
    providers = list(provider_result.scalars().all())
    await _ensure_provider_catalog_models(db, providers)
    model_result = await db.execute(
        select(AIModel).where(AIModel.provider_id.in_([p.id for p in providers])).order_by(AIModel.display_name)
    ) if providers else None
    models_by_provider: dict[UUID, list[AIModel]] = {}
    if model_result:
        for model in model_result.scalars().all():
            models_by_provider.setdefault(model.provider_id, []).append(model)

    route_result = await db.execute(select(AIRoute).where(AIRoute.task_type.in_(list(ROUTE_DEFINITIONS))))
    routes_by_task = {route.task_type: route for route in route_result.scalars().all()}
    route_responses = [
        RouteResponse(
            task_type=task_type,
            label=definition["label"],
            model_task_type=definition["model_task_type"],
            primary_model_id=routes_by_task.get(task_type).primary_model_id if routes_by_task.get(task_type) else None,
        )
        for task_type, definition in ROUTE_DEFINITIONS.items()
    ]
    return ProviderListResponse(
        providers=[
            ProviderResponse(
                id=provider.id,
                name=provider.name,
                provider_key=_provider_key(provider),
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
            label=ROUTE_DEFINITIONS[CHAT_ROUTE]["label"],
            model_task_type=ROUTE_DEFINITIONS[CHAT_ROUTE]["model_task_type"],
            primary_model_id=routes_by_task.get(CHAT_ROUTE).primary_model_id
            if routes_by_task.get(CHAT_ROUTE) else None,
        ) if routes_by_task.get(CHAT_ROUTE) else None,
        routes=route_responses,
        catalog=_provider_catalog(providers),
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


async def _enabled_models_for_task(
    db: AsyncSession,
    model_task_type: str,
    excluded_model_ids: set[UUID] | None = None,
) -> list[AIModel]:
    excluded_model_ids = excluded_model_ids or set()
    provider_types = {OPENAI_COMPATIBLE}
    if model_task_type == VOICE_TRANSCRIPTION_MODEL_TASK:
        provider_types = {OPENAI_COMPATIBLE, ELEVENLABS}

    conditions = [
        AIModel.enabled == "true",
        AIProvider.enabled == "true",
        AIProvider.provider_type.in_(provider_types),
    ]
    if excluded_model_ids:
        conditions.append(~AIModel.id.in_(excluded_model_ids))

    result = await db.execute(
        select(AIModel)
        .join(AIProvider, AIProvider.id == AIModel.provider_id)
        .where(*conditions)
        .order_by(AIProvider.name, AIModel.display_name)
    )
    models = [model for model in result.scalars().all() if _model_task_type(model) == model_task_type]
    if model_task_type == CHAT_MODEL_TASK:
        return sorted(models, key=_chat_model_sort_key)
    return sorted(models, key=lambda model: (model.display_name or model.model_name or "").lower())


async def _enabled_chat_models(db: AsyncSession) -> list[AIModel]:
    return await _enabled_models_for_task(db, CHAT_MODEL_TASK)


async def _reconcile_route(db: AsyncSession, route_task_type: str) -> None:
    await db.flush()
    model_task_type = _route_model_task_type(route_task_type)
    models = await _enabled_models_for_task(db, model_task_type)
    route_result = await db.execute(select(AIRoute).where(AIRoute.task_type == route_task_type))
    route = route_result.scalar_one_or_none()

    if not models:
        if route:
            route.enabled = "false"
        return

    enabled_ids = {model.id for model in models}
    primary = route.primary_model_id if route and route.primary_model_id in enabled_ids else models[0].id

    if not route:
        route = AIRoute(
            id=uuid.uuid4(),
            task_type=route_task_type,
            primary_model_id=primary,
            temperature=0.3,
            max_tokens=2000,
            system_prompt=CANONICAL_SYSTEM_PROMPT if route_task_type == CHAT_ROUTE else "",
            enabled="true",
        )
        db.add(route)

    route.primary_model_id = primary
    route.enabled = "true"


async def _reconcile_routes(db: AsyncSession) -> None:
    await _reconcile_route(db, CHAT_ROUTE)
    route_result = await db.execute(
        select(AIRoute).where(
            AIRoute.task_type.in_([task for task in ROUTE_DEFINITIONS if task != CHAT_ROUTE])
        )
    )
    for route in route_result.scalars().all():
        await _reconcile_route(db, route.task_type)


async def _reconcile_chat_route(db: AsyncSession) -> None:
    await _reconcile_route(db, CHAT_ROUTE)


async def _reconcile_routes_before_model_delete(db: AsyncSession, deleted_model_ids: set[UUID]) -> None:
    if not deleted_model_ids:
        return

    route_result = await db.execute(select(AIRoute).where(AIRoute.primary_model_id.in_(deleted_model_ids)))
    routes = list(route_result.scalars().all())
    if not routes:
        return

    for route in routes:
        model_task_type = ROUTE_DEFINITIONS.get(route.task_type, {}).get("model_task_type", CHAT_MODEL_TASK)
        remaining_models = await _enabled_models_for_task(
            db,
            model_task_type,
            deleted_model_ids,
        )
        if not remaining_models:
            await db.execute(update(AIUsageLog).where(AIUsageLog.route_id == route.id).values(route_id=None))
            await db.execute(update(AITrace).where(AITrace.route_id == route.id).values(route_id=None))
            await db.delete(route)
            continue
        remaining_ids = {model.id for model in remaining_models}
        route.primary_model_id = route.primary_model_id if route.primary_model_id in remaining_ids else remaining_models[0].id
        route.enabled = "true"
    await db.flush()


async def _sync_provider_models(db: AsyncSession, provider: AIProvider, api_key: str) -> int:
    if provider.provider_type == ELEVENLABS:
        discovered_models = _provider_catalog_models(provider.base_url, provider.provider_type, _provider_key(provider))
    else:
        discovered_models = _merge_catalog_models(
            provider.base_url,
            await _fetch_available_models(provider.base_url, api_key),
            provider.provider_type,
            _provider_key(provider),
        )
    if not discovered_models:
        raise RuntimeError("No models were returned by this provider.")

    for discovered in discovered_models:
        await _upsert_discovered_model(db, provider, discovered)

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

    name, base_url, provider_type, provider_key = _resolve_provider_request(req)
    provider = await _get_provider(db, req.provider_id) if req.provider_id else None
    if not provider:
        existing = await db.execute(select(AIProvider).where(AIProvider.name == name))
        provider = existing.scalar_one_or_none()

    if not provider:
        provider = AIProvider(
            id=uuid.uuid4(),
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            auth_type="key_vault_secret",
            secret_reference=_secret_name(name),
            enabled=_bool_string(req.enabled),
            capabilities={"provider_key": provider_key} if provider_key else {},
        )
        db.add(provider)
    else:
        provider.name = name
        provider.provider_type = provider_type
        provider.base_url = base_url
        provider.auth_type = "key_vault_secret"
        provider.enabled = _bool_string(req.enabled)
        if not provider.secret_reference:
            provider.secret_reference = _secret_name(name)
        capabilities = _provider_capabilities(provider)
        if provider_key:
            capabilities["provider_key"] = provider_key
        else:
            capabilities.pop("provider_key", None)
        provider.capabilities = capabilities

    await db.flush()

    sync_result: ModelSyncResponse | None = None
    if provider.enabled == "true":
        api_key = req.api_key or await _stored_api_key(provider)
        if not api_key:
            raise HTTPException(status_code=400, detail="API key is required to enable this provider.")
        try:
            model_count = await _sync_provider_models(db, provider, api_key)
        except Exception as exc:
            await db.rollback()
            raise HTTPException(status_code=400, detail=f"Provider model sync failed: {exc}") from exc
        sync_result = ModelSyncResponse(
            success=True,
            message=f"Synced {model_count} models.",
            model_count=model_count,
        )

    await _save_provider_secret(provider, req.api_key)

    await _reconcile_routes(db)
    await db.commit()
    return await _provider_payload(db, sync_result)


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
        secret_status = await _api_key_status(provider)
        if secret_status == "saved":
            await delete_secret(provider.secret_reference)
        elif secret_status in {"error", "vault_not_configured"}:
            raise HTTPException(status_code=500, detail="Provider API key status could not be verified before deletion.")

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
    if req.enabled is not None:
        model.enabled = _bool_string(req.enabled)
    if req.task_type is not None:
        task_type = req.task_type.strip()
        if task_type not in set(MODEL_TASK_LABELS):
            raise HTTPException(status_code=400, detail=f"Unsupported model task type: {req.task_type}.")
        config = dict(model.config_json) if isinstance(model.config_json, dict) else {}
        config["task_type"] = task_type
        model.config_json = config
        if task_type != CHAT_MODEL_TASK:
            model.supports_tools = "false"
            model.supports_json_schema = "false"
    await _reconcile_routes(db)
    await db.commit()
    return await _provider_payload(db)


@router.patch("/route", response_model=ProviderListResponse)
async def update_model_route(
    req: RouteUpdateRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    _require_admin(auth)
    route_task_type = req.task_type.strip() or CHAT_ROUTE
    route_definition = _route_definition(route_task_type)
    required_model_task_type = route_definition["model_task_type"]
    primary = await _get_model(db, req.primary_model_id)
    provider = await _get_provider(db, primary.provider_id)
    if provider.enabled != "true" or primary.enabled != "true":
        raise HTTPException(status_code=400, detail="Only enabled provider models can be selected.")
    if _model_task_type(primary) != required_model_task_type:
        label = route_definition["label"].lower()
        raise HTTPException(status_code=400, detail=f"Only {label} models can be selected for this route.")

    result = await db.execute(select(AIRoute).where(AIRoute.task_type == route_task_type))
    route = result.scalar_one_or_none()
    if not route:
        route = AIRoute(
            id=uuid.uuid4(),
            task_type=route_task_type,
            primary_model_id=primary.id,
            temperature=0.3,
            max_tokens=2000,
            system_prompt=CANONICAL_SYSTEM_PROMPT if route_task_type == CHAT_ROUTE else "",
            enabled="true",
        )
        db.add(route)
    route.primary_model_id = primary.id
    route.enabled = "true"
    await db.commit()
    return await _provider_payload(db)
