#!/usr/bin/env bash
# Browser end-to-end tests (verbeterlijst 42, plan §12) against the real stack:
#   PostgreSQL (a fresh, throw-away database) + the API (uvicorn via `shc-api serve`) + the built web
#   app served by `vite preview`, which proxies /api (incl. the WebSocket) to the API.
# No worker and no hardware: tests put devices, button mappings and alerts straight into the database
# with e2e/seed.py. Everything runs on free ports and is removed afterwards.
#
# Usage (from anywhere):
#   apps/web/e2e/run-stack.sh [playwright test arguments...]      e.g.  --trace off  or  -g offline
#
# Environment:
#   SHC_E2E_PG          server URL without database name (default postgresql://postgres@127.0.0.1:5432);
#                       the role must be allowed to CREATE DATABASE
#   SHC_E2E_PYTHON      Python with apps/api and integrations installed (default apps/api/.venv/bin/python,
#                       else python3)
#   SHC_E2E_SKIP_BUILD  1 = use the existing dist/ instead of `npm run build`
#   SHC_E2E_KEEP        1 = keep the database and the log folder after the run
#
# Internal subcommands, used by the offline test to stop and restart the API of a running stack
# (they need the environment this script exports to Playwright):
#   run-stack.sh api-stop | api-start
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$E2E_DIR/.." && pwd)"
REPO_DIR="$(cd "$WEB_DIR/../.." && pwd)"

log() { printf '[e2e] %s\n' "$*" >&2; }

pick_python() {
  if [[ -n "${SHC_E2E_PYTHON:-}" ]]; then
    printf '%s' "$SHC_E2E_PYTHON"
  elif [[ -x "$REPO_DIR/apps/api/.venv/bin/python" ]]; then
    printf '%s' "$REPO_DIR/apps/api/.venv/bin/python"
  else
    command -v python3
  fi
}

wait_http() { # url seconds what
  local url="$1" limit="$2" what="$3" i
  for ((i = 0; i < limit * 4; i++)); do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then return 0; fi
    sleep 0.25
  done
  log "$what antwoordt niet op $url"
  return 1
}

api_start() {
  : "${E2E_STATE_DIR:?}" "${E2E_API_PORT:?}" "${E2E_PYTHON:?}"
  # Detached and with all output in the log: a caller waiting for our stdout must not wait for uvicorn.
  "$E2E_PYTHON" -m shc_api.cli serve --host 127.0.0.1 --port "$E2E_API_PORT" \
    </dev/null >>"$E2E_STATE_DIR/api.log" 2>&1 &
  echo $! >"$E2E_STATE_DIR/api.pid"
  wait_http "http://127.0.0.1:$E2E_API_PORT/api/v1/health" 60 "De API"
}

api_stop() {
  : "${E2E_STATE_DIR:?}"
  local pid i
  pid="$(cat "$E2E_STATE_DIR/api.pid" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 0
  kill -TERM "$pid" 2>/dev/null || true
  for ((i = 0; i < 40; i++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$E2E_STATE_DIR/api.pid"
}

case "${1:-}" in
  api-stop) api_stop; exit 0 ;;
  api-start) api_start; exit 0 ;;
esac

PY="$(pick_python)"
PG="${SHC_E2E_PG:-postgresql://postgres@127.0.0.1:5432}"
PG="${PG%/}"
"$PY" -c 'import shc_api, shc_integrations' 2>/dev/null || {
  log "$PY kan shc_api niet importeren. Installeer eerst: pip install -e integrations -e apps/api"
  exit 2
}

free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }

STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/shc-e2e.XXXXXX")"
DB_NAME="shc_e2e_$(basename "$STATE_DIR" | tr -dc '[:alnum:]' | tr '[:upper:]' '[:lower:]')"
API_PORT="$(free_port)"
WEB_PORT="$(free_port)"
while [[ "$WEB_PORT" == "$API_PORT" ]]; do WEB_PORT="$(free_port)"; done
mkdir -p "$STATE_DIR/backups" "$STATE_DIR/data"

pg_admin() { # sql
  PG_URL="$PG/postgres" SQL="$1" "$PY" - <<'EOF'
import asyncio, os
import asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["PG_URL"])
    try:
        await conn.execute(os.environ["SQL"])
    finally:
        await conn.close()
asyncio.run(main())
EOF
}

WEB_PID=""
STATUS=1
# shellcheck disable=SC2329 # called by the EXIT trap
cleanup() {
  set +e
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null
  E2E_STATE_DIR="$STATE_DIR" api_stop
  if [[ "$STATUS" != 0 ]]; then
    log "Mislukt. Laatste regels van de API en van vite preview:"
    tail -n 60 "$STATE_DIR/api.log" >&2 2>/dev/null
    tail -n 20 "$STATE_DIR/web.log" >&2 2>/dev/null
  fi
  if [[ "${SHC_E2E_KEEP:-0}" == 1 ]]; then
    log "Bewaard: database $DB_NAME, logboeken in $STATE_DIR"
  else
    pg_admin "DROP DATABASE IF EXISTS $DB_NAME WITH (FORCE)" >/dev/null 2>&1
    rm -rf "$STATE_DIR"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

log "Database $DB_NAME op $PG"
pg_admin "CREATE DATABASE $DB_NAME"

export SHC_DATABASE_URL="${PG/postgresql:\/\//postgresql+asyncpg://}/$DB_NAME"
SHC_SECRET_KEY="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SHC_SECRET_KEY
export SHC_BACKUP_DIR="$STATE_DIR/backups"
export SHC_DATA_DIR="$STATE_DIR/data"
export SHC_COOKIE_SECURE=auto
export SHC_FORWARDED_ALLOW_IPS=127.0.0.1
unset SHC_LAN_URL SHC_REMOTE_URL SHC_PAIRING_ALLOWED_HOSTS SHC_NOTIFY_URL
export SHC_LOG_LEVEL=INFO
export E2E_STATE_DIR="$STATE_DIR" E2E_API_PORT="$API_PORT" E2E_PYTHON="$PY"
export E2E_API_URL="http://127.0.0.1:$API_PORT"
export E2E_BASE_URL="http://127.0.0.1:$WEB_PORT"
export E2E_RUN_STACK="$E2E_DIR/run-stack.sh"

cd "$WEB_DIR"
if [[ "${SHC_E2E_SKIP_BUILD:-0}" != 1 || ! -f dist/index.html ]]; then
  log "Web-app bouwen (npm run build)"
  npm run build >"$STATE_DIR/build.log" 2>&1 || { cat "$STATE_DIR/build.log" >&2; exit 1; }
fi

log "API starten op poort $API_PORT"
api_start || exit 1

log "Web-app (vite preview) starten op poort $WEB_PORT"
node node_modules/vite/bin/vite.js preview --config e2e/vite.preview.config.ts --port "$WEB_PORT" \
  </dev/null >"$STATE_DIR/web.log" 2>&1 &
WEB_PID=$!
wait_http "$E2E_BASE_URL/" 30 "vite preview" || exit 1
wait_http "$E2E_BASE_URL/api/v1/health" 10 "De proxy naar de API" || exit 1

log "Playwright starten"
set +e
npx playwright test --config playwright.config.ts "$@"
STATUS=$?
set -e
exit "$STATUS"
