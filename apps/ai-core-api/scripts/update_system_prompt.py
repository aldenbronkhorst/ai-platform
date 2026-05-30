#!/usr/bin/env python3
"""Production-safe migration: update ai_routes.general_chat system_prompt.

This script explicitly corrects the system prompt for the general_chat route
in the production database. It:
  - Finds the active general_chat route (fails if none exists)
  - Verifies old Odoo/ERP wording is gone after update
  - Reports exactly which route was updated
  - Is safe to run repeatedly (idempotent)

Indended use:
    PYTHONPATH=/app python3 scripts/update_system_prompt.py

Exit codes:
    0 - prompt already correct (no change needed)
    1 - prompt was updated
    2 - no general_chat route found (error)
"""
import asyncio
import sys
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.models import AIRoute
from app.services.model_router import CANONICAL_SYSTEM_PROMPT

# Phrases that indicate Odoo/ERP-centric identity we want to replace
ODOO_CENTRIC_PHRASES = [
    "Odoo assistant",
    "ERP assistant",
    "integrated with Odoo",
    "Odoo ERP",
    "Odoo-related",
    "fully integrated into Odoo",
    "operational assistant",
    "you are an odoo",
    "you are an erp",
]


def _contains_odoo_centric_wording(text: str) -> bool:
    lower = text.lower()
    for phrase in ODOO_CENTRIC_PHRASES:
        if phrase.lower() in lower:
            return True
    return False


async def update_prompt():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AIRoute).where(
                AIRoute.task_type == "general_chat",
                AIRoute.enabled == "true",
            )
        )
        route = result.scalar_one_or_none()

        if not route:
            print("ERROR: No active general_chat route found in database.", file=sys.stderr)
            print("Cannot update system prompt. Has the seed script been run?", file=sys.stderr)
            sys.exit(2)

        old_prompt = route.system_prompt or ""

        if old_prompt == CANONICAL_SYSTEM_PROMPT:
            print(f"OK: Route '{route.task_type}' (id={route.id}) already has the canonical prompt.")
            print("No update needed.")
            sys.exit(0)

        # Check if old prompt contains Odoo-centric wording
        if _contains_odoo_centric_wording(old_prompt):
            print(f"WARNING: Route '{route.task_type}' contains Odoo/ERP-centric wording.")
            print(f"Old prompt preview: {old_prompt[:120]!r}")

        print(f"Updating route '{route.task_type}' (id={route.id})...")
        route.system_prompt = CANONICAL_SYSTEM_PROMPT
        await db.commit()

        # Verify
        await db.refresh(route)
        new_prompt = route.system_prompt
        if new_prompt == CANONICAL_SYSTEM_PROMPT:
            print(f"SUCCESS: Route '{route.task_type}' system_prompt updated.")
            print(f"New prompt preview: {new_prompt[:120]!r}")
        else:
            print(f"ERROR: Prompt update verification failed.", file=sys.stderr)
            sys.exit(2)

        # Verify old Odoo-centric wording is gone
        if _contains_odoo_centric_wording(new_prompt):
            print(f"ERROR: Updated prompt STILL contains Odoo-centric wording!", file=sys.stderr)
            sys.exit(2)

        print("OK: No Odoo/ERP-centric wording remains in the prompt.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(update_prompt())
