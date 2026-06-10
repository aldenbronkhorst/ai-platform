from uuid import UUID
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict

from app.core.security import api_key_auth, require_role
from app.core.database import get_db
from app.models.models import AIProvider, AIModel, AIRoute, AIUsageLog
from app.services.model_router import get_enabled_route, build_foundry_client

router = APIRouter(prefix="/ai-config", tags=["AI Configuration"])


# ── Schemas ──
MODEL_FIELD_CONFIG = ConfigDict(protected_namespaces=())
MODEL_FIELD_RESPONSE_CONFIG = ConfigDict(from_attributes=True, protected_namespaces=())


class ProviderCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str
    auth_type: str = "key_vault_secret"
    secret_reference: Optional[str] = None
    enabled: str = "true"

class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    secret_reference: Optional[str] = None
    enabled: Optional[str] = None

class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    provider_type: str
    base_url: str
    auth_type: str
    enabled: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

class ModelCreate(BaseModel):
    model_config = MODEL_FIELD_CONFIG

    provider_id: UUID
    display_name: str
    model_name: str
    deployment_name: str
    model_family: Optional[str] = None
    model_version: Optional[str] = None
    supports_tools: str = "false"
    supports_json_schema: str = "false"
    context_window: Optional[int] = None
    enabled: str = "true"

class ModelUpdate(BaseModel):
    display_name: Optional[str] = None
    enabled: Optional[str] = None

class ModelResponse(BaseModel):
    model_config = MODEL_FIELD_RESPONSE_CONFIG

    id: UUID
    provider_id: UUID
    display_name: str
    model_name: str
    deployment_name: str
    model_family: Optional[str]
    model_version: Optional[str]
    supports_tools: str
    supports_json_schema: str
    context_window: Optional[int]
    enabled: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

class RouteCreate(BaseModel):
    model_config = MODEL_FIELD_CONFIG

    task_type: str
    primary_model_id: UUID
    fallback_model_id: Optional[UUID] = None
    temperature: float = 0.3
    max_tokens: int = 2000
    system_prompt: Optional[str] = None
    enabled: str = "true"

class RouteUpdate(BaseModel):
    model_config = MODEL_FIELD_CONFIG

    primary_model_id: Optional[UUID] = None
    fallback_model_id: Optional[UUID] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None
    enabled: Optional[str] = None

class RouteResponse(BaseModel):
    model_config = MODEL_FIELD_RESPONSE_CONFIG

    id: UUID
    task_type: str
    primary_model_id: UUID
    fallback_model_id: Optional[UUID]
    temperature: float
    max_tokens: int
    system_prompt: Optional[str]
    enabled: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

class TestConsoleRequest(BaseModel):
    task_type: str = "general_chat"
    prompt: str = "Say hello and confirm the model route is working."

class TestConsoleResponse(BaseModel):
    model_config = MODEL_FIELD_RESPONSE_CONFIG

    success: bool
    response: Optional[str] = None
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    latency_ms: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    error: Optional[str] = None

class UsageLogResponse(BaseModel):
    model_config = MODEL_FIELD_RESPONSE_CONFIG

    id: UUID
    timestamp: Optional[datetime]
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    task_type: Optional[str]
    model_id: Optional[UUID]
    provider_id: Optional[UUID]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: Optional[int]
    cost_estimate: Optional[float]
    status: str
    error_message: Optional[str]

class ConfigSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    providers: List[ProviderResponse]
    models: List[ModelResponse]
    routes: List[RouteResponse]


# ── Provider CRUD ──

@router.get("/providers", response_model=List[ProviderResponse])
async def list_providers(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    result = await db.execute(select(AIProvider).order_by(AIProvider.created_at))
    return result.scalars().all()


@router.post("/providers", response_model=ProviderResponse, status_code=201)
async def create_provider(
    req: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin"])),
):
    existing = await db.execute(select(AIProvider).where(AIProvider.name == req.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Provider with this name already exists")
    provider = AIProvider(**req.model_dump())
    db.add(provider)
    await db.flush()
    await db.refresh(provider)
    return provider


@router.patch("/providers/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: UUID,
    req: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin"])),
):
    result = await db.execute(select(AIProvider).where(AIProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(provider, key, value)
    await db.flush()
    await db.refresh(provider)
    return provider


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin"])),
):
    await db.execute(delete(AIProvider).where(AIProvider.id == provider_id))


# ── Model CRUD ──

@router.get("/models", response_model=List[ModelResponse])
async def list_models(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    result = await db.execute(select(AIModel).order_by(AIModel.created_at))
    return result.scalars().all()


@router.post("/models", response_model=ModelResponse, status_code=201)
async def create_model(
    req: ModelCreate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin", "AIPlatform.Developer"])),
):
    model = AIModel(**req.model_dump())
    db.add(model)
    await db.flush()
    await db.refresh(model)
    return model


@router.patch("/models/{model_id}", response_model=ModelResponse)
async def update_model(
    model_id: UUID,
    req: ModelUpdate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin", "AIPlatform.Developer"])),
):
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(model, key, value)
    await db.flush()
    await db.refresh(model)
    return model


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin", "AIPlatform.Developer"])),
):
    await db.execute(delete(AIModel).where(AIModel.id == model_id))


# ── Route CRUD ──

@router.get("/routes", response_model=List[RouteResponse])
async def list_routes(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    result = await db.execute(select(AIRoute).order_by(AIRoute.created_at))
    return result.scalars().all()


@router.post("/routes", response_model=RouteResponse, status_code=201)
async def create_route(
    req: RouteCreate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin", "AIPlatform.Developer"])),
):
    existing = await db.execute(select(AIRoute).where(AIRoute.task_type == req.task_type))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Route for this task_type already exists")
    route = AIRoute(**req.model_dump())
    db.add(route)
    await db.flush()
    await db.refresh(route)
    return route


@router.patch("/routes/{route_id}", response_model=RouteResponse)
async def update_route(
    route_id: UUID,
    req: RouteUpdate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin", "AIPlatform.Developer"])),
):
    result = await db.execute(select(AIRoute).where(AIRoute.id == route_id))
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(route, key, value)
    await db.flush()
    await db.refresh(route)
    return route


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(
    route_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(require_role(["AIPlatform.Admin", "AIPlatform.Developer"])),
):
    await db.execute(delete(AIRoute).where(AIRoute.id == route_id))


# ── Summary ──

@router.get("/summary", response_model=ConfigSummaryResponse)
async def get_config_summary(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    providers = (await db.execute(select(AIProvider).order_by(AIProvider.created_at))).scalars().all()
    models = (await db.execute(select(AIModel).order_by(AIModel.created_at))).scalars().all()
    routes = (await db.execute(select(AIRoute).order_by(AIRoute.created_at))).scalars().all()
    return ConfigSummaryResponse(
        providers=list(providers),
        models=list(models),
        routes=list(routes),
    )


# ── Test Console ──

@router.post("/test", response_model=TestConsoleResponse)
async def test_route(
    req: TestConsoleRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    try:
        route, model, provider = await get_enabled_route(db, req.task_type)
    except Exception as e:
        return TestConsoleResponse(success=False, error=str(e))

    client = await build_foundry_client(provider, model)
    temperature = float(route.temperature) if route.temperature is not None else 0.3
    max_tokens = route.max_tokens or 2000

    messages = [{"role": "user", "content": req.prompt}]
    if route.system_prompt:
        messages = [{"role": "system", "content": route.system_prompt}] + messages

    result = await client.chat_completion(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    if result.get("error"):
        return TestConsoleResponse(
            success=False,
            error=result.get("message", "Unknown error"),
            latency_ms=result.get("latency_ms"),
            model_provider=provider.name,
            model_name=model.display_name,
        )

    return TestConsoleResponse(
        success=True,
        response=result.get("content", ""),
        model_provider=provider.name,
        model_name=model.display_name,
        latency_ms=result.get("latency_ms"),
        prompt_tokens=result.get("prompt_tokens", 0),
        completion_tokens=result.get("completion_tokens", 0),
        total_tokens=result.get("total_tokens", 0),
    )


# ── Usage Logs ──

@router.get("/usage", response_model=List[UsageLogResponse])
async def list_usage_logs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    result = await db.execute(
        select(AIUsageLog).order_by(AIUsageLog.timestamp.desc()).limit(limit)
    )
    return result.scalars().all()
