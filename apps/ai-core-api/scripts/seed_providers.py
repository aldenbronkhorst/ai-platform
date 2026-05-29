"""Seed the initial Microsoft Foundry provider, Kimi K2.6 model, and general_chat route.

Idempotent: creates records if missing, updates mutable config fields (system_prompt,
temperature, max_tokens) if records already exist. Does NOT overwrite secrets or
runtime usage data. Does NOT duplicate providers/models/routes.

Single source of truth for system prompt: app.services.model_router.CANONICAL_SYSTEM_PROMPT.
"""
import asyncio
import uuid
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.models import AIProvider, AIModel, AIRoute
from app.services.model_router import CANONICAL_SYSTEM_PROMPT

PROVIDER_DATA = {
    "name": "Microsoft Foundry",
    "provider_type": "azure_foundry",
    "base_url": "https://fnd-ai-platform-prod-san-001.services.ai.azure.com",
    "auth_type": "key_vault_secret",
    "secret_reference": "model-provider-foundry-primary-key",
    "enabled": "true",
}

MODEL_DATA = {
    "display_name": "Kimi K2.6",
    "model_name": "Kimi-K2.6",
    "deployment_name": "kimi-k2-6-general-chat",
    "model_family": "Kimi",
    "model_version": "2026-04-20",
    "supports_tools": "true",
    "supports_json_schema": "false",
    "context_window": 262144,
    "enabled": "true",
}

ROUTE_DATA = {
    "task_type": "general_chat",
    "temperature": 0.3,
    "max_tokens": 2000,
    "system_prompt": CANONICAL_SYSTEM_PROMPT,
    "enabled": "true",
}


async def seed():
    async with AsyncSessionLocal() as db:
        # ── 1. Provider: upsert ──
        existing_prov = await db.execute(
            select(AIProvider).where(AIProvider.name == PROVIDER_DATA["name"])
        )
        provider = existing_prov.scalar_one_or_none()
        if provider:
            changed = []
            for field in ("base_url", "provider_type", "auth_type", "enabled"):
                new_val = PROVIDER_DATA[field]
                if getattr(provider, field) != new_val:
                    setattr(provider, field, new_val)
                    changed.append(field)
            if changed:
                print(f"Provider '{provider.name}' updated fields: {', '.join(changed)}")
            else:
                print(f"Provider '{provider.name}' already up-to-date.")
        else:
            provider = AIProvider(id=uuid.uuid4(), **PROVIDER_DATA)
            db.add(provider)
            await db.flush()
            print(f"Provider '{PROVIDER_DATA['name']}' created.")

        # ── 2. Model: upsert ──
        existing_model = await db.execute(
            select(AIModel).where(
                AIModel.provider_id == provider.id,
                AIModel.model_name == MODEL_DATA["model_name"],
            )
        )
        model = existing_model.scalar_one_or_none()
        if model:
            changed = []
            for field in ("display_name", "deployment_name", "model_version",
                          "supports_tools", "supports_json_schema", "context_window",
                          "enabled"):
                new_val = MODEL_DATA[field]
                if getattr(model, field) != new_val:
                    setattr(model, field, new_val)
                    changed.append(field)
            if changed:
                print(f"Model '{model.display_name}' updated fields: {', '.join(changed)}")
            else:
                print(f"Model '{model.display_name}' already up-to-date.")
        else:
            model = AIModel(id=uuid.uuid4(), provider_id=provider.id, **MODEL_DATA)
            db.add(model)
            await db.flush()
            print(f"Model '{MODEL_DATA['display_name']}' created.")

        # ── 3. Route: upsert (always update system_prompt to canonical) ──
        existing_route = await db.execute(
            select(AIRoute).where(AIRoute.task_type == ROUTE_DATA["task_type"])
        )
        route = existing_route.scalar_one_or_none()
        if route:
            changed = []
            for field in ("temperature", "max_tokens", "enabled"):
                new_val = ROUTE_DATA[field]
                if getattr(route, field) != new_val:
                    setattr(route, field, new_val)
                    changed.append(field)
            if route.system_prompt != CANONICAL_SYSTEM_PROMPT:
                old_preview = route.system_prompt[:80] if route.system_prompt else "(empty)"
                route.system_prompt = CANONICAL_SYSTEM_PROMPT
                changed.append("system_prompt")
                print(f"Route '{route.task_type}' system_prompt updated. "
                      f"Old preview: {old_preview!r}")
            if not changed:
                print(f"Route '{route.task_type}' already up-to-date.")
            else:
                print(f"Route '{route.task_type}' updated fields: {', '.join(changed)}")
        else:
            route = AIRoute(id=uuid.uuid4(), primary_model_id=model.id, **ROUTE_DATA)
            db.add(route)
            print(f"Route '{ROUTE_DATA['task_type']}' created.")

        await db.commit()
        print("Seed complete: Microsoft Foundry \u2192 Kimi K2.6 \u2192 general_chat route")


if __name__ == "__main__":
    asyncio.run(seed())
