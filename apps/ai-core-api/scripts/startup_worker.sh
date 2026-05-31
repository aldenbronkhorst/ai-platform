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

echo "Starting memory worker..."
export PYTHONPATH=/app
exec python3 scripts/run_worker.py
