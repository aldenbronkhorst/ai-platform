#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
ODOO_PORT="${ODOO_PORT:-8010}"
WEB_PORT="${WEB_PORT:-5173}"
CHECK_INTERVAL_SECONDS="${LOCAL_STACK_CHECK_INTERVAL_SECONDS:-20}"
MAX_FAILED_CHECKS="${LOCAL_STACK_MAX_FAILED_CHECKS:-3}"
RESTART_DELAY_SECONDS="${LOCAL_STACK_RESTART_DELAY_SECONDS:-10}"
STARTUP_GRACE_SECONDS="${LOCAL_STACK_STARTUP_GRACE_SECONDS:-240}"
LOG_DIR="$ROOT_DIR/.local/logs"
SUPERVISOR_LOG="$LOG_DIR/local-stack-supervisor.log"

mkdir -p "$LOG_DIR"

timestamp() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$SUPERVISOR_LOG"
}

port_pids() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

stop_pid_tree() {
  local pid="$1"
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  pkill -TERM -P "$pid" >/dev/null 2>&1 || true
  kill -TERM "$pid" >/dev/null 2>&1 || true

  for _ in $(seq 1 15); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  pkill -KILL -P "$pid" >/dev/null 2>&1 || true
  kill -KILL "$pid" >/dev/null 2>&1 || true
}

stop_local_port_listeners() {
  local port pid
  for port in "$API_PORT" "$ODOO_PORT" "$WEB_PORT"; do
    for pid in $(port_pids "$port"); do
      log "Stopping existing listener on port $port (pid $pid)"
      stop_pid_tree "$pid"
    done
  done
}

probe_url() {
  local url="$1"
  curl -fsS --max-time 8 "$url" >/dev/null
}

local_stack_ready() {
  probe_url "http://127.0.0.1:$ODOO_PORT/health" &&
    probe_url "http://127.0.0.1:$API_PORT/health/ready" &&
    probe_url "http://127.0.0.1:$WEB_PORT"
}

cd "$ROOT_DIR"
export LOCAL_POSTGRES_FIREWALL="${LOCAL_POSTGRES_FIREWALL:-true}"
export UVICORN_RELOAD="${UVICORN_RELOAD:-false}"

log "Local AI Platform supervisor started"

while true; do
  if local_stack_ready; then
    log "Local stack is already ready; monitoring existing services"
    failed_checks=0
    while true; do
      sleep "$CHECK_INTERVAL_SECONDS"
      if local_stack_ready; then
        failed_checks=0
        continue
      fi
      failed_checks=$((failed_checks + 1))
      log "Local stack readiness probe failed ($failed_checks/$MAX_FAILED_CHECKS)"
      if (( failed_checks >= MAX_FAILED_CHECKS )); then
        log "Restarting local stack after repeated readiness failures"
        stop_local_port_listeners
        break
      fi
    done
  fi

  stop_local_port_listeners

  log "Starting local AI Platform stack"
  ./scripts/dev-local-stack.sh >>"$LOG_DIR/local-stack.log" 2>&1 &
  stack_pid="$!"
  stack_started_at="$(date +%s)"
  failed_checks=0

  while kill -0 "$stack_pid" >/dev/null 2>&1; do
    sleep "$CHECK_INTERVAL_SECONDS"
    now="$(date +%s)"
    if (( now - stack_started_at < STARTUP_GRACE_SECONDS )); then
      continue
    fi

    if local_stack_ready; then
      failed_checks=0
      continue
    fi

    failed_checks=$((failed_checks + 1))
    log "Local stack readiness probe failed ($failed_checks/$MAX_FAILED_CHECKS)"
    if (( failed_checks >= MAX_FAILED_CHECKS )); then
      log "Restarting local stack after repeated readiness failures"
      stop_pid_tree "$stack_pid"
      wait "$stack_pid" >/dev/null 2>&1 || true
      break
    fi
  done

  if ! kill -0 "$stack_pid" >/dev/null 2>&1; then
    wait "$stack_pid" >/dev/null 2>&1 || true
    log "Local stack process exited"
  fi

  log "Restarting in $RESTART_DELAY_SECONDS seconds"
  sleep "$RESTART_DELAY_SECONDS"
done
