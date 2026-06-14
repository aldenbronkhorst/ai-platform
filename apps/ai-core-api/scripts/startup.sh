#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Seeding tools..."
set +e
PYTHONPATH=/app python3 scripts/seed_tools.py
TOOL_SEED_EXIT=$?
if [ $TOOL_SEED_EXIT -ne 0 ]; then
  echo "Seed tools failed (exit $TOOL_SEED_EXIT) but continuing; will retry on next restart"
fi
set -e

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
