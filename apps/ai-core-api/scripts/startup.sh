#!/bin/bash
set -e

echo "Stamping alembic to 001_initial to avoid stale version conflicts..."
alembic stamp 001_initial 2>&1 || echo "Stamp may already be current"

echo "Running database migrations..."
alembic upgrade head

echo "Seeding initial data..."
set +e
PYTHONPATH=/app python3 scripts/seed_providers.py
SEED_EXIT=$?
set -e
if [ $SEED_EXIT -ne 0 ]; then
  echo "Seed failed (exit $SEED_EXIT) but continuing; will retry on next restart"
  python3 -c "import sys; print('sys.path:', sys.path); import os; print('cwd:', os.getcwd()); print('app dir exists:', os.path.isdir('/app/app'))"
fi

echo "Ensuring system prompt is up-to-date..."
set +e
PYTHONPATH=/app python3 scripts/update_system_prompt.py
PROMPT_EXIT=$?
set -e
if [ $PROMPT_EXIT -eq 2 ]; then
  echo "WARNING: update_system_prompt.py failed (no general_chat route). This is expected on first deploy."
elif [ $PROMPT_EXIT -eq 1 ]; then
  echo "System prompt was updated to canonical version."
elif [ $PROMPT_EXIT -eq 0 ]; then
  echo "System prompt already correct."
fi

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
