"""Seed the model provider, chat model, and single chat route.

Idempotent: creates records if missing, updates mutable config fields.
Does NOT duplicate providers/models/routes.
"""
import asyncio
import uuid
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.models import AIProvider, AIModel, AIRoute
from app.services.model_router import CANONICAL_SYSTEM_PROMPT

PROVIDERS_TO_SEED = [
    {
        "name": "Kimi",
        "provider_type": "openai_compatible",
        "base_url": "https://api.moonshot.ai/v1",
        "auth_type": "key_vault_secret",
        "secret_reference": "model-provider-kimi-api-key",
        "capabilities": {"api_key_env_vars": ["KIMI_API_KEY", "MOONSHOT_API_KEY"]},
        "enabled": "true",
    },
    {
        "name": "DeepSeek",
        "provider_type": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "auth_type": "key_vault_secret",
        "secret_reference": "model-provider-deepseek-api-key",
        "capabilities": {"api_key_env_vars": ["DEEPSEEK_API_KEY"]},
        "enabled": "true",
    }
]

MODELS_TO_SEED = [
    {
        "provider_name": "Kimi",
        "display_name": "Kimi K2.6",
        "model_name": "kimi-k2.6",
        "deployment_name": "kimi-k2.6",
        "model_family": "Kimi",
        "model_version": "K2.6",
        "supports_tools": "true",
        "supports_json_schema": "false",
        "context_window": 262144,
        "enabled": "true",
        "config_json": {
            "cost_tier": "low",
            "latency_tier": "medium",
            "quality_tier": "advanced",
            "request_options": {
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        }
    },
    {
        "provider_name": "DeepSeek",
        "display_name": "DeepSeek V4 Flash",
        "model_name": "deepseek-v4-flash",
        "deployment_name": "deepseek-v4-flash",
        "model_family": "DeepSeek",
        "model_version": "V4 Flash",
        "supports_tools": "true",
        "supports_json_schema": "true",
        "context_window": 1000000,
        "enabled": "true",
        "config_json": {
            "cost_tier": "low",
            "latency_tier": "fast",
            "quality_tier": "advanced",
            "request_options": {
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        }
    }
]

ROUTES_TO_SEED = [
    {
        "task_type": "general_chat",
        "primary_model_name": "kimi-k2.6",
        "fallback_model_name": "deepseek-v4-flash",
        "temperature": 0.3,
        "max_tokens": 2000,
        "system_prompt": CANONICAL_SYSTEM_PROMPT,
        "enabled": "true"
    }
]


async def seed():
    async with AsyncSessionLocal() as db:
        # ── 1. Providers: upsert loop ──
        provider_map = {}
        for p_data in PROVIDERS_TO_SEED:
            existing_prov = await db.execute(
                select(AIProvider).where(AIProvider.name == p_data["name"])
            )
            provider = existing_prov.scalar_one_or_none()
            if provider:
                changed = []
                for field in ("base_url", "provider_type", "auth_type", "enabled", "secret_reference", "capabilities"):
                    new_val = p_data[field]
                    if getattr(provider, field) != new_val:
                        setattr(provider, field, new_val)
                        changed.append(field)
                if changed:
                    print(f"Provider '{provider.name}' updated fields: {', '.join(changed)}")
                else:
                    print(f"Provider '{provider.name}' already up-to-date.")
            else:
                provider = AIProvider(id=uuid.uuid4(), **p_data)
                db.add(provider)
                await db.flush()
                print(f"Provider '{p_data['name']}' created.")
            provider_map[provider.name] = provider

        # ── 2. Models: upsert loop ──
        model_name_map = {}
        for m_data in MODELS_TO_SEED:
            provider_name = m_data["provider_name"]
            prov_obj = provider_map[provider_name]
            
            existing_model = await db.execute(
                select(AIModel).where(
                    AIModel.provider_id == prov_obj.id,
                    AIModel.model_name == m_data["model_name"],
                )
            )
            model = existing_model.scalar_one_or_none()
            if model:
                changed = []
                for field in ("display_name", "deployment_name", "model_version",
                              "supports_tools", "supports_json_schema", "context_window",
                              "enabled", "config_json"):
                    new_val = m_data[field]
                    if getattr(model, field) != new_val:
                        setattr(model, field, new_val)
                        changed.append(field)
                if changed:
                    print(f"Model '{model.display_name}' updated fields: {', '.join(changed)}")
                else:
                    print(f"Model '{model.display_name}' already up-to-date.")
            else:
                # Remove provider_name as it is not a DB column
                db_model_data = {k: v for k, v in m_data.items() if k != "provider_name"}
                model = AIModel(id=uuid.uuid4(), provider_id=prov_obj.id, **db_model_data)
                db.add(model)
                await db.flush()
                print(f"Model '{m_data['display_name']}' created.")
            
            model_name_map[m_data["model_name"]] = model

        # ── 3. Routes: upsert loop ──
        for r_data in ROUTES_TO_SEED:
            existing_route = await db.execute(
                select(AIRoute).where(AIRoute.task_type == r_data["task_type"])
            )
            route = existing_route.scalar_one_or_none()

            prim_model = model_name_map.get(r_data["primary_model_name"])
            fallback_model = model_name_map.get(r_data.get("fallback_model_name"))

            if route:
                changed = []
                for field in ("temperature", "max_tokens", "enabled", "system_prompt"):
                    new_val = r_data[field]
                    if getattr(route, field) != new_val:
                        setattr(route, field, new_val)
                        changed.append(field)
                if prim_model and route.primary_model_id != prim_model.id:
                    route.primary_model_id = prim_model.id
                    changed.append("primary_model_id")
                if fallback_model and route.fallback_model_id != fallback_model.id:
                    route.fallback_model_id = fallback_model.id
                    changed.append("fallback_model_id")
                
                if changed:
                    print(f"Route '{route.task_type}' updated fields: {', '.join(changed)}")
                else:
                    print(f"Route '{route.task_type}' already up-to-date.")
            else:
                route = AIRoute(
                    id=uuid.uuid4(),
                    task_type=r_data["task_type"],
                    primary_model_id=prim_model.id if prim_model else None,
                    fallback_model_id=fallback_model.id if fallback_model else None,
                    temperature=r_data["temperature"],
                    max_tokens=r_data["max_tokens"],
                    system_prompt=r_data["system_prompt"],
                    enabled=r_data["enabled"]
                )
                db.add(route)
                print(f"Route '{r_data['task_type']}' created.")

        await db.commit()
        print("Seed complete: seeded Providers, Models and Routes")


if __name__ == "__main__":
    asyncio.run(seed())
