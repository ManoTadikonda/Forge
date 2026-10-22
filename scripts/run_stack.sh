#!/usr/bin/env bash
# Start the full Forge stack locally: PostgreSQL, Redis, router, API and worker.
#
# No container runtime is required. PostgreSQL is an embedded server managed by
# `pgserver`; Redis is built from source by scripts/build_redis.sh on first run.
# Both store data under .forge/ and survive restarts.
#
#   scripts/run_stack.sh start     start everything (idempotent)
#   scripts/run_stack.sh stop      stop services, keep all data
#   scripts/run_stack.sh status    show what is running
#   scripts/run_stack.sh logs      tail every service log
#   scripts/run_stack.sh clean     stop and DELETE all local data (destructive)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
. "$ROOT/scripts/env.sh"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
RUN_DIR="$ROOT/.forge/run"
LOG_DIR="$ROOT/.forge/logs"
REDIS_BIN="$ROOT/.tooling/redis/bin/redis-server"

API_PORT="${FORGE_API_PORT:-8000}"
ROUTER_PORT="${FORGE_ROUTER_PORT:-8098}"
ROUTER_ADMIN_PORT="${FORGE_ROUTER_ADMIN_PORT:-8099}"
REDIS_PORT="${FORGE_REDIS_PORT:-6399}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# ---------------------------------------------------------------- configuration
# Development defaults. Every one of these is overridable from the environment,
# and none of them is a production credential.
export FORGE_ENVIRONMENT="${FORGE_ENVIRONMENT:-development}"
export FORGE_LOG_FORMAT="${FORGE_LOG_FORMAT:-json}"
export FORGE_REDIS_URL="${FORGE_REDIS_URL:-redis://127.0.0.1:$REDIS_PORT/0}"
export FORGE_ROUTER_ADMIN_URL="${FORGE_ROUTER_ADMIN_URL:-http://127.0.0.1:$ROUTER_ADMIN_PORT}"
export FORGE_ROUTER_PORT="$ROUTER_PORT"
export FORGE_ROUTER_ADMIN_PORT="$ROUTER_ADMIN_PORT"
# The process builder is not a kernel sandbox; it must be enabled deliberately.
export FORGE_ALLOW_UNSANDBOXED_BUILDS="${FORGE_ALLOW_UNSANDBOXED_BUILDS:-true}"

if [ -z "${FORGE_SECRET_KEY:-}" ]; then
  # Generated once per machine and kept out of git. Never a fixed default.
  KEY_FILE="$ROOT/.forge/secret_key"
  if [ ! -f "$KEY_FILE" ]; then
    "$PYTHON" -c "import secrets; print(secrets.token_urlsafe(48))" > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
  fi
  FORGE_SECRET_KEY="$(cat "$KEY_FILE")"
  export FORGE_SECRET_KEY
fi

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }

pid_file() { echo "$RUN_DIR/$1.pid"; }

is_running() {
  local file; file="$(pid_file "$1")"
  [ -f "$file" ] && kill -0 "$(cat "$file")" 2>/dev/null
}

wait_for_http() {
  local url="$1" name="$2" tries="${3:-60}"
  for _ in $(seq 1 "$tries"); do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then return 0; fi
    sleep 0.5
  done
  fail "$name did not become reachable at $url"
  return 1
}

start_redis() {
  if [ ! -x "$REDIS_BIN" ]; then
    warn "Redis is not built. Building it now (a few minutes, once)."
    "$ROOT/scripts/build_redis.sh"
  fi
  if "$ROOT/.tooling/redis/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Redis already running on $REDIS_PORT"
    return
  fi
  log "Starting Redis on $REDIS_PORT"
  mkdir -p "$ROOT/.forge/redis"
  "$REDIS_BIN" --port "$REDIS_PORT" --daemonize yes \
    --dir "$ROOT/.forge/redis" --save '' --appendonly no \
    --pidfile "$(pid_file redis)" --logfile "$LOG_DIR/redis.log" \
    --bind 127.0.0.1
  sleep 0.5
}

start_service() {
  local name="$1"; shift
  if is_running "$name"; then
    log "$name already running (pid $(cat "$(pid_file "$name")"))"
    return
  fi
  log "Starting $name"
  "$@" >> "$LOG_DIR/$name.log" 2>&1 &
  echo $! > "$(pid_file "$name")"
}

stop_service() {
  local name="$1" file
  file="$(pid_file "$name")"
  if [ -f "$file" ]; then
    local pid; pid="$(cat "$file")"
    if kill -0 "$pid" 2>/dev/null; then
      log "Stopping $name (pid $pid)"
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi
}

cmd_start() {
  log "Forge local stack"
  start_redis

  log "Applying database migrations"
  ( cd "$ROOT/packages/forge-core" && "$PYTHON" -m alembic upgrade head ) \
    >> "$LOG_DIR/migrations.log" 2>&1
  log "Migrations at head"

  start_service router "$PYTHON" -m forge_router.app
  wait_for_http "http://127.0.0.1:$ROUTER_ADMIN_PORT/health" "router admin"
  log "Router: proxy :$ROUTER_PORT, admin :$ROUTER_ADMIN_PORT"

  start_service api "$PYTHON" -m uvicorn forge_api.main:app \
    --host 127.0.0.1 --port "$API_PORT" --log-level warning
  wait_for_http "http://127.0.0.1:$API_PORT/health" "api"
  log "API: http://127.0.0.1:$API_PORT (docs at /docs)"

  start_service worker "$PYTHON" -m forge_worker.runtime
  sleep 1
  if is_running worker; then log "Worker running"; else fail "Worker failed; see $LOG_DIR/worker.log"; fi

  echo
  log "Stack is up. Logs: $LOG_DIR"
  if [ -n "${FORGE_BOOTSTRAP_ADMIN_EMAIL:-}" ]; then
    log "Bootstrap admin: $FORGE_BOOTSTRAP_ADMIN_EMAIL"
  else
    warn "No bootstrap admin configured. Create a user with:"
    warn "  $PYTHON -m forge_api.cli create-user --email you@example.com --role ADMIN"
  fi
}

cmd_stop() {
  for name in worker api router; do stop_service "$name"; done
  if "$ROOT/.tooling/redis/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Stopping Redis"
    "$ROOT/.tooling/redis/bin/redis-cli" -p "$REDIS_PORT" shutdown nosave 2>/dev/null || true
  fi
  rm -f "$(pid_file redis)"
  # PostgreSQL is left running: it is shared, cheap, and holds the durable state
  # that must survive a stack restart. 'clean' stops and removes it.
  log "Stopped. Database and all data preserved."
}

cmd_status() {
  printf '%-10s %-10s %s\n' SERVICE STATUS DETAIL
  for name in router api worker; do
    if is_running "$name"; then
      printf '%-10s %-10s pid %s\n' "$name" running "$(cat "$(pid_file "$name")")"
    else
      printf '%-10s %-10s %s\n' "$name" stopped -
    fi
  done
  if "$ROOT/.tooling/redis/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    printf '%-10s %-10s port %s\n' redis running "$REDIS_PORT"
  else
    printf '%-10s %-10s %s\n' redis stopped -
  fi
  if [ -d "$ROOT/.forge/pgdata" ]; then
    printf '%-10s %-10s %s\n' postgres present "$ROOT/.forge/pgdata"
  else
    printf '%-10s %-10s %s\n' postgres absent -
  fi
}

cmd_logs() { tail -n 40 -f "$LOG_DIR"/*.log; }

cmd_clean() {
  if [ "${1:-}" != "--yes" ]; then
    fail "'clean' deletes the database, artifacts, workspaces and all local data."
    fail "Re-run as: scripts/run_stack.sh clean --yes"
    exit 1
  fi
  cmd_stop
  log "Stopping PostgreSQL and deleting all local data"
  "$PYTHON" - <<'PY' || true
import contextlib, pathlib
with contextlib.suppress(Exception):
    import pgserver
    data = pathlib.Path(".forge/pgdata").resolve()
    if data.exists():
        pgserver.get_server(data).cleanup()
PY
  rm -rf "$ROOT/.forge"
  log "All local data removed. Run 'start' for a clean stack."
}

case "${1:-start}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  logs)   cmd_logs ;;
  clean)  shift; cmd_clean "${1:-}" ;;
  restart) cmd_stop; cmd_start ;;
  *) echo "usage: $0 {start|stop|restart|status|logs|clean --yes}" >&2; exit 2 ;;
esac
