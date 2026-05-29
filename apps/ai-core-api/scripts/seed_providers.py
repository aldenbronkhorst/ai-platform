"""Seed the initial Microsoft Foundry provider, Kimi K2.6 model, and general_chat route."""
import asyncio
import uuid
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.models import AIProvider, AIModel, AIRoute


async def seed():
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(AIProvider).where(AIProvider.name == "Microsoft Foundry"))
        if existing.scalar_one_or_none():
            print("Provider already seeded, skipping.")
            return

        # 1. Provider
        provider = AIProvider(
            id=uuid.uuid4(),
            name="Microsoft Foundry",
            provider_type="azure_foundry",
            base_url="https://fnd-ai-platform-prod-san-001.services.ai.azure.com",
            auth_type="key_vault_secret",
            secret_reference="model-provider-foundry-primary-key",
            enabled="true",
        )
        db.add(provider)
        await db.flush()

        # 2. Model
        model = AIModel(
            id=uuid.uuid4(),
            provider_id=provider.id,
            display_name="Kimi K2.6",
            model_name="Kimi-K2.6",
            deployment_name="kimi-k2-6-general-chat",
            model_family="Kimi",
            model_version="2026-04-20",
            supports_tools="true",
            supports_json_schema="false",
            context_window=262144,
            enabled="true",
        )
        db.add(model)
        await db.flush()

        # 3. Route
        route = AIRoute(
            id=uuid.uuid4(),
            task_type="general_chat",
            primary_model_id=model.id,
            fallback_model_id=None,
            temperature=0.3,
            max_tokens=2000,
            system_prompt="You are an AI Platform assistant integrated with Odoo ERP. "
                          "You help with business operations, timesheet audits, and ledger checks. "
                          "Be concise, professional, and use natural language. "
                          "When the user references business data, guide them through the available workflows.",
            enabled="true",
        )
        db.add(route)
        await db.commit()
        print("Seeded: Microsoft Foundry → Kimi K2.6 → general_chat route")


if __name__ == "__main__":
    asyncio.run(seed())
