#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
ODOO_PORT="${ODOO_PORT:-8010}"
WEB_PORT="${WEB_PORT:-5173}"
UVICORN_RELOAD="${UVICORN_RELOAD:-false}"
API_STARTUP_ATTEMPTS="${API_STARTUP_ATTEMPTS:-180}"
ODOO_STARTUP_ATTEMPTS="${ODOO_STARTUP_ATTEMPTS:-60}"
WEB_STARTUP_ATTEMPTS="${WEB_STARTUP_ATTEMPTS:-60}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ai-platform-prod-san-001}"
API_CONTAINER_APP="${API_CONTAINER_APP:-ca-ai-platform-api-prod-san-001}"

POSTGRES_SERVER_NAME="${POSTGRES_SERVER_NAME:-psql-ai-platform-prod-san-001}"
POSTGRES_HOST="${POSTGRES_HOST:-$POSTGRES_SERVER_NAME.postgres.database.azure.com}"
POSTGRES_DB="${POSTGRES_DB:-aicore}"
POSTGRES_USER="${POSTGRES_USER:-aiplatformadmin}"
LOCAL_POSTGRES_FIREWALL="${LOCAL_POSTGRES_FIREWALL:-true}"
POSTGRES_FIREWALL_REFRESH_SECONDS="${POSTGRES_FIREWALL_REFRESH_SECONDS:-60}"
LOCAL_USER="$(whoami | tr -cd '[:alnum:]_-')"
POSTGRES_FIREWALL_RULE_NAME="${POSTGRES_FIREWALL_RULE_NAME:-LocalDev-${LOCAL_USER:-user}}"
KEY_VAULT_URI="${KEY_VAULT_URI:-https://kvaiplatformprodsan001.vault.azure.net/}"
STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME:-staiplatformprodsan001}"
ENTRA_TENANT_ID="${ENTRA_TENANT_ID:-03af606c-d85a-48ff-ad4b-a5a8895a6d98}"
ENTRA_CLIENT_ID="${ENTRA_CLIENT_ID:-fcefb508-bb9d-4d5d-b1c5-6d2ef04c0208}"
PORTAL_CLIENT_ID="${PORTAL_CLIENT_ID:-ff6a9526-c27a-42a6-b317-56060d11b14e}"
MICROSOFT_ADMIN_CLIENT_ID="${MICROSOFT_ADMIN_CLIENT_ID:-8a178920-de9e-41cf-af4e-c3012fc3bbd2}"
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="${AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT:-https://fr-ai-platform-prod-san-001.cognitiveservices.azure.com/}"
DOCUMENT_OCR_PROVIDER="${DOCUMENT_OCR_PROVIDER:-azure_document_intelligence}"
DOCUMENT_OCR_READ_MODEL_ID="${DOCUMENT_OCR_READ_MODEL_ID:-prebuilt-read}"
DOCUMENT_OCR_LAYOUT_MODEL_ID="${DOCUMENT_OCR_LAYOUT_MODEL_ID:-prebuilt-layout}"

LOG_DIR="$ROOT_DIR/.local/logs"
mkdir -p "$LOG_DIR"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

port_in_use() {
  lsof -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

require_free_port() {
  local port="$1"
  local name="$2"
  local env_name
  env_name="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')"
  if port_in_use "$port"; then
    echo "Port $port is already in use; cannot start $name." >&2
    echo "Stop the existing process or rerun with ${env_name}_PORT set to another port." >&2
    exit 1
  fi
}

secret() {
  local name="$1"
  az containerapp secret show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$API_CONTAINER_APP" \
    --secret-name "$name" \
    --query value \
    -o tsv
}

sha_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

ensure_python_env() {
  local app_dir="$1"
  local requirements="$2"
  local venv="$app_dir/.venv"
  local stamp="$venv/.requirements.sha"
  local current_sha
  current_sha="$(sha_file "$requirements")"

  if [[ ! -x "$venv/bin/python" ]]; then
    python3 -m venv "$venv"
  fi

  if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$current_sha" ]]; then
    "$venv/bin/python" -m pip install --upgrade pip
    "$venv/bin/python" -m pip install -r "$requirements"
    echo "$current_sha" > "$stamp"
  fi
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local attempts="${3:-60}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep 1
  done
  echo "$name did not become ready: $url" >&2
  return 1
}

is_truthy() {
  [[ "${1:-}" =~ ^(1|true|yes|on)$ ]]
}

current_public_ip() {
  curl -fsS https://api.ipify.org
}

UVICORN_RELOAD_ARG=""
if is_truthy "$UVICORN_RELOAD"; then
  UVICORN_RELOAD_ARG="--reload"
fi

ensure_postgres_firewall_access() {
  local current_ip
  current_ip="$(current_public_ip)"
  if [[ ! "$current_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Could not determine a valid public IPv4 address for PostgreSQL firewall access." >&2
    return 1
  fi

  echo "Ensuring PostgreSQL firewall allows this Mac ($current_ip)..."
  if az postgres flexible-server firewall-rule show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$POSTGRES_SERVER_NAME" \
    --rule-name "$POSTGRES_FIREWALL_RULE_NAME" \
    --only-show-errors \
    -o none >/dev/null 2>&1; then
    az postgres flexible-server firewall-rule update \
      --resource-group "$RESOURCE_GROUP" \
      --name "$POSTGRES_SERVER_NAME" \
      --rule-name "$POSTGRES_FIREWALL_RULE_NAME" \
      --start-ip-address "$current_ip" \
      --end-ip-address "$current_ip" \
      --only-show-errors \
      -o none
  else
    az postgres flexible-server firewall-rule create \
      --resource-group "$RESOURCE_GROUP" \
      --name "$POSTGRES_SERVER_NAME" \
      --rule-name "$POSTGRES_FIREWALL_RULE_NAME" \
      --start-ip-address "$current_ip" \
      --end-ip-address "$current_ip" \
      --only-show-errors \
      -o none
  fi

  LAST_POSTGRES_FIREWALL_IP="$current_ip"
}

postgres_firewall_watch() {
  local current_ip
  LAST_POSTGRES_FIREWALL_IP="${LAST_POSTGRES_FIREWALL_IP:-}"
  while sleep "$POSTGRES_FIREWALL_REFRESH_SECONDS"; do
    current_ip="$(current_public_ip 2>/dev/null || true)"
    if [[ ! "$current_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      continue
    fi
    if [[ "$current_ip" != "$LAST_POSTGRES_FIREWALL_IP" ]]; then
      echo "Public IP changed; refreshing PostgreSQL firewall access..."
      ensure_postgres_firewall_access || true
    fi
  done
}

wait_for_postgres() {
  local attempts="${1:-60}"
  for _ in $(seq 1 "$attempts"); do
    if (
      cd "$ROOT_DIR/apps/ai-core-api"
      .venv/bin/python - <<'PY'
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings


async def main():
    engine = create_async_engine(get_settings().database_url, connect_args={"timeout": 8})
    try:
        async def probe():
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))

        await asyncio.wait_for(probe(), timeout=12)
    finally:
        await engine.dispose()


asyncio.run(main())
PY
    ) >/dev/null 2>&1; then
      echo "PostgreSQL is reachable."
      return 0
    fi
    sleep 2
  done

  echo "PostgreSQL did not become reachable." >&2
  return 1
}

cleanup() {
  local code=$?
  trap - INT TERM EXIT
  echo
  echo "Stopping local AI Platform services..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait >/dev/null 2>&1 || true
  exit "$code"
}

require_command az
require_command curl
require_command lsof
require_command npm
require_command python3

require_free_port "$API_PORT" api
require_free_port "$ODOO_PORT" odoo
require_free_port "$WEB_PORT" web

echo "Checking Azure login..."
az account show >/dev/null

if is_truthy "$LOCAL_POSTGRES_FIREWALL"; then
  ensure_postgres_firewall_access || exit 1
fi

echo "Checking Microsoft localhost redirect..."
if ! az ad app show --id "$PORTAL_CLIENT_ID" --query "spa.redirectUris" -o tsv | grep -q "http://localhost:$WEB_PORT"; then
  echo "The Microsoft portal app does not list http://localhost:$WEB_PORT as a SPA redirect URI." >&2
  echo "Add that redirect URI in Azure before using local Microsoft sign-in." >&2
  exit 1
fi

echo "Preparing Python environments..."
ensure_python_env "$ROOT_DIR/apps/ai-core-api" "$ROOT_DIR/apps/ai-core-api/requirements.txt"
ensure_python_env "$ROOT_DIR/apps/odoo-connector-api" "$ROOT_DIR/apps/odoo-connector-api/requirements.txt"

if [[ ! -d "$ROOT_DIR/apps/web-portal/node_modules" ]]; then
  echo "Installing portal dependencies..."
  (cd "$ROOT_DIR/apps/web-portal" && npm ci --workspaces=false)
fi

echo "Fetching live configuration from Azure Container Apps..."
API_KEY="$(secret api-key)"
POSTGRES_PASSWORD="$(secret keyvault-dsn)"
ODOO_CONNECTOR_API_KEY="$(secret odoo-connector-api-key)"
AZURE_STORAGE_ACCOUNT_KEY="${AZURE_STORAGE_ACCOUNT_KEY:-$(az storage account keys list --resource-group "$RESOURCE_GROUP" --account-name "$STORAGE_ACCOUNT_NAME" --query '[0].value' -o tsv)}"

export APP_ENV=development
export DEBUG=false
export POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export KEY_VAULT_URI STORAGE_ACCOUNT_NAME
export AZURE_STORAGE_ACCOUNT_KEY
export API_KEY
export ODOO_CONNECTOR_URL="http://127.0.0.1:$ODOO_PORT"
export ODOO_CONNECTOR_API_KEY
export ENTRA_TENANT_ID ENTRA_CLIENT_ID
export MICROSOFT_ADMIN_CLIENT_ID
export MICROSOFT_ADMIN_APP_DISPLAY_NAME="${MICROSOFT_ADMIN_APP_DISPLAY_NAME:-AI Platform Microsoft Admin}"
export AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT DOCUMENT_OCR_PROVIDER DOCUMENT_OCR_READ_MODEL_ID DOCUMENT_OCR_LAYOUT_MODEL_ID
export AZURE_SEARCH_ENABLE="${AZURE_SEARCH_ENABLE:-false}"
export HEALTH_CHECK_DEEP="${HEALTH_CHECK_DEEP:-false}"

wait_for_postgres 60

PIDS=()
trap cleanup INT TERM EXIT

if is_truthy "$LOCAL_POSTGRES_FIREWALL"; then
  postgres_firewall_watch >"$LOG_DIR/postgres-firewall-watch.log" 2>&1 &
  PIDS+=("$!")
fi

echo "Starting local Odoo connector on http://127.0.0.1:$ODOO_PORT ..."
(
  cd "$ROOT_DIR/apps/odoo-connector-api"
  APP_ENV=development \
  DEBUG=false \
  INTERNAL_API_KEY="$ODOO_CONNECTOR_API_KEY" \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$ODOO_PORT" ${UVICORN_RELOAD_ARG:+"$UVICORN_RELOAD_ARG"}
) >"$LOG_DIR/odoo-connector.log" 2>&1 &
PIDS+=("$!")

wait_for_url "http://127.0.0.1:$ODOO_PORT/health" "Odoo connector" "$ODOO_STARTUP_ATTEMPTS"

echo "Starting local AI core API on http://127.0.0.1:$API_PORT ..."
(
  cd "$ROOT_DIR/apps/ai-core-api"
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" ${UVICORN_RELOAD_ARG:+"$UVICORN_RELOAD_ARG"}
) >"$LOG_DIR/ai-core-api.log" 2>&1 &
PIDS+=("$!")

wait_for_url "http://127.0.0.1:$API_PORT/health/ready" "AI core API" "$API_STARTUP_ATTEMPTS"

echo "Starting local web portal on http://localhost:$WEB_PORT ..."
(
  cd "$ROOT_DIR/apps/web-portal"
  VITE_API_BASE_URL="http://localhost:$API_PORT" \
  VITE_ENTRA_CLIENT_ID="$PORTAL_CLIENT_ID" \
  VITE_ENTRA_TENANT_ID="$ENTRA_TENANT_ID" \
  npm run dev -- --host 127.0.0.1 --port "$WEB_PORT"
) >"$LOG_DIR/web-portal.log" 2>&1 &
PIDS+=("$!")

wait_for_url "http://127.0.0.1:$WEB_PORT" "Web portal" "$WEB_STARTUP_ATTEMPTS"

cat <<EOF

Local AI Platform is running.

Open: http://localhost:$WEB_PORT
API:  http://127.0.0.1:$API_PORT
Odoo: http://127.0.0.1:$ODOO_PORT

Logs:
- $LOG_DIR/web-portal.log
- $LOG_DIR/ai-core-api.log
- $LOG_DIR/odoo-connector.log

Press Ctrl+C in this terminal to stop all local services.
EOF

wait
