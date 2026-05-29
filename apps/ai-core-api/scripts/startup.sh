#!/bin/bash
set -e

echo "Stamping alembic at 001 to recover from any stale version..."
python3 -c "
import asyncio
from app.core.database import AsyncSessionLocal
from sqlalchemy import text
async def run():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(\"SELECT version_num FROM alembic_version\"))
        v = r.scalar()
        if v and v != '001_initial':
            await db.execute(text(\"DELETE FROM alembic_version\"))
            await db.execute(text(\"INSERT INTO alembic_version (version_num) VALUES ('001_initial')\"))
            await db.commit()
            print(f'Stamped alembic from {v} back to 001_initial')
        else:
            print(f'Alembic version OK: {v}')
asyncio.run(run())
" 2>&1

echo "Running database migrations..."
alembic upgrade head

echo "Seeding initial data..."
python3 scripts/seed_providers.py

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
