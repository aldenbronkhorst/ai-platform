#!/bin/bash
set -e

echo "Checking database migration state..."
CURRENT_REV=$(alembic current 2>&1 || true)
if echo "$CURRENT_REV" | grep -q "Current revision(s):.*None\|No current revision"; then
  echo "Fresh database detected, running all migrations from scratch"
else
  echo "Existing migration state found: $CURRENT_REV"
fi

echo "Running database migrations..."
alembic upgrade head

echo "Seeding initial data..."
set +e
PYTHONPATH=/app python3 scripts/seed_providers.py
SEED_EXIT=$?
if [ $SEED_EXIT -ne 0 ]; then
  echo "Seed providers failed (exit $SEED_EXIT) but continuing; will retry on next restart"
  python3 -c "import sys; print('sys.path:', sys.path); import os; print('cwd:', os.getcwd()); print('app dir exists:', os.path.isdir('/app/app'))"
fi
echo "Seeding tools..."
PYTHONPATH=/app python3 scripts/seed_tools.py
TOOL_SEED_EXIT=$?
if [ $TOOL_SEED_EXIT -ne 0 ]; then
  echo "Seed tools failed (exit $TOOL_SEED_EXIT) but continuing; will retry on next restart"
fi
set -e

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
