#!/bin/bash
set -e

echo "Stamping alembic to 001_initial to avoid stale version conflicts..."
alembic stamp 001_initial 2>&1 || echo "Stamp may already be current"

echo "Running database migrations..."
alembic upgrade head

echo "Seeding initial data..."
PYTHONPATH=/app python3 scripts/seed_providers.py

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
