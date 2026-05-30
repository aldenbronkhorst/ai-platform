import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import get_settings
from app.models.models import AIRule, AICompanyFact

settings = get_settings()

RULES = [
    {
        "title": "Odoo Artifact Policy",
        "body": (
            "Intermediate, debug, OCR, parsed, or scratch artifacts must be stored in AI Platform artifact storage. "
            "Attach to Odoo only when: the user explicitly requested that file; or the file is a final business deliverable; "
            "or the workflow specifically defines it as an Odoo attachment. "
            "Raw CSV, OCR text, JSON, or debug files must not be attached to Odoo unless explicitly requested."
        ),
        "scope_type": "global",
        "status": "active",
        "priority": 10,
    },
    {
        "title": "Odoo Chatter Policy",
        "body": (
            "Do not post raw OCR, CSV, JSON, or long tabular data into Odoo chatter unless the user explicitly asks for that exact raw content to be posted. "
            "Default chatter output should be a short human-readable summary."
        ),
        "scope_type": "global",
        "status": "active",
        "priority": 10,
    },
    {
        "title": "User Identity Policy",
        "body": (
            "Direct user-triggered actions must use the requesting user's connected account wherever possible. "
            "Scheduled or autonomous automations must use service identities and record created_by, owner, service_identity, job_id."
        ),
        "scope_type": "global",
        "status": "active",
        "priority": 5,
    },
    {
        "title": "Cosmetic Connection Credit Note Reconciliation",
        "body": (
            "Use STK-CODE to match customer product code. Use GROSS as unit price. "
            "Group quantities across all PDFs. Store OCR outputs in AI artifacts, not Odoo. "
            "Attach only final workbook unless raw exports requested."
        ),
        "scope_type": "workflow",
        "workflow": "credit_note_reconciliation",
        "supplier": "Cosmetic Connection",
        "status": "active",
        "priority": 20,
    },
    {
        "title": "Currency Presentation Policy",
        "body": (
            "Always present monetary values using the connected Odoo company's currency. "
            "Do not default to US dollar ($). "
            "If the connected account shows ZAR, prepend 'R' symbol with a space and use thousand separators. "
            "If currency is unknown, ask the user before quoting amounts."
        ),
        "scope_type": "global",
        "status": "active",
        "priority": 20,
    },
]

FACTS = [
    {
        "key": "default_currency",
        "value": "ZAR",
        "category": "finance",
    },
    {
        "key": "odoo_primary_db",
        "value": "Lots Lots More Production",
        "category": "systems",
    },
    {
        "key": "ai_platform_region",
        "value": "southafricanorth",
        "category": "infrastructure",
    },
    {
        "key": "default_currency_format",
        "value": "R 1,234.56 (South African notation; symbol before amount, space before numeric)",
        "category": "finance",
    },
]


async def seed_rules():
    engine = create_async_engine(settings.database_url, future=True)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        from sqlalchemy import select

        for rule_data in RULES:
            result = await session.execute(select(AIRule).where(AIRule.title == rule_data["title"]))
            existing = result.scalar_one_or_none()
            if not existing:
                rule = AIRule(**rule_data)
                session.add(rule)
                print(f"Added rule: {rule_data['title']}")
            else:
                print(f"Rule already exists: {rule_data['title']}")

        for fact_data in FACTS:
            result = await session.execute(select(AICompanyFact).where(AICompanyFact.key == fact_data["key"]))
            existing = result.scalar_one_or_none()
            if not existing:
                fact = AICompanyFact(**fact_data)
                session.add(fact)
                print(f"Added fact: {fact_data['key']}")
            else:
                print(f"Fact already exists: {fact_data['key']}")

        await session.commit()
    await engine.dispose()
    print("Rule/fact seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed_rules())
