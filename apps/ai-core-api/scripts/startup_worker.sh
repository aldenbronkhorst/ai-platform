#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting memory worker..."
export PYTHONPATH=/app
exec python3 scripts/run_worker.py
