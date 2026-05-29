#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Seeding initial data..."
python3 scripts/seed_providers.py

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
