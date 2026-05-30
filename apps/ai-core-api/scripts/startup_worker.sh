#!/bin/bash
set -e

echo "Stamping alembic to 001_initial to avoid stale version conflicts..."
alembic stamp 001_initial 2>&1 || echo "Stamp may already be current"

echo "Running database migrations..."
alembic upgrade head

echo "Starting memory worker..."
export PYTHONPATH=/app
exec python3 scripts/run_worker.py
