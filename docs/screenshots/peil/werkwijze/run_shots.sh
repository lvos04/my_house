#!/usr/bin/env bash
# Schermafdrukken "voor" (Peil-migratie) van de my_house-web-app tegen de echte stack:
# wegwerp-PostgreSQL-database + API (shc-api serve) + gebouwde web-app achter vite preview
# (zelfde opzet als apps/web/e2e/run-stack.sh). Buiten de repo; niets wordt gecommit behalve de PNG's.
#
#   run_shots.sh <uitvoermap>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="/home/transcode/truenas-v2/my_house"
WEB="$REPO/apps/web"
OUT="${1:?uitvoermap}"
PY="/home/transcode/truenas-v2/.lokaal/my_house-ci/venv/bin/python"
PG="postgresql://postgres@127.0.0.1:5432"

log() { printf '[shots] %s\n' "$*" >&2; }
wait_http() { local url="$1" limit="$2" i; for ((i = 0; i < limit * 4; i++)); do curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null && return 0; sleep 0.25; done; log "geen antwoord: $url"; return 1; }
free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }
pg_admin() { PG_URL="$PG/postgres" SQL="$1" "$PY" - <<'EOF'
import asyncio, os, asyncpg
async def main():
    c = await asyncpg.connect(os.environ["PG_URL"])
    try: await c.execute(os.environ["SQL"])
    finally: await c.close()
asyncio.run(main())
EOF
}

STATE_DIR="$(mktemp -d "$HERE/tmp/state.XXXXXX")"
DB_NAME="my_house_peil_$(basename "$STATE_DIR" | tr -dc '[:alnum:]' | tr '[:upper:]' '[:lower:]')"
API_PORT="$(free_port)"; WEB_PORT="$(free_port)"
mkdir -p "$STATE_DIR/backups" "$STATE_DIR/data" "$OUT"
WEB_PID=""
HB_PID=""
cleanup() {
  set +e
  [[ -n "$HB_PID" ]] && kill "$HB_PID" 2>/dev/null
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null
  [[ -f "$STATE_DIR/api.pid" ]] && kill "$(cat "$STATE_DIR/api.pid")" 2>/dev/null
  sleep 1
  pg_admin "DROP DATABASE IF EXISTS $DB_NAME WITH (FORCE)" >/dev/null 2>&1
  log "logs: $STATE_DIR"
}
trap cleanup EXIT

pg_admin "CREATE DATABASE $DB_NAME"
export SHC_DATABASE_URL="postgresql+asyncpg://postgres@127.0.0.1:5432/$DB_NAME"
SHC_SECRET_KEY="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"; export SHC_SECRET_KEY
export SHC_BACKUP_DIR="$STATE_DIR/backups" SHC_DATA_DIR="$STATE_DIR/data" SHC_COOKIE_SECURE=auto SHC_FORWARDED_ALLOW_IPS=127.0.0.1 SHC_LOG_LEVEL=INFO
unset SHC_LAN_URL SHC_REMOTE_URL SHC_PAIRING_ALLOWED_HOSTS SHC_NOTIFY_URL
export E2E_STATE_DIR="$STATE_DIR" E2E_API_PORT="$API_PORT" E2E_PYTHON="$PY"
export E2E_API_URL="http://127.0.0.1:$API_PORT" E2E_BASE_URL="http://127.0.0.1:$WEB_PORT"
export PEIL_SEED="$HERE/seed_peil.py" PEIL_OUT="$OUT" PEIL_RUNNER="$HERE/run_shots.sh"

api_start() {
  "$PY" -m shc_api.cli serve --host 127.0.0.1 --port "$API_PORT" </dev/null >>"$STATE_DIR/api.log" 2>&1 &
  echo $! >"$STATE_DIR/api.pid"
  wait_http "http://127.0.0.1:$API_PORT/api/v1/health" 60
}

cd "$WEB"
if [[ "${PEIL_SKIP_BUILD:-0}" != 1 || ! -f dist/index.html ]]; then
  log "web-app bouwen"; nice -n 10 npm run build >"$STATE_DIR/build.log" 2>&1 || { cat "$STATE_DIR/build.log" >&2; exit 1; }
fi
log "API op $API_PORT"; api_start
"$PY" "$HERE/seed_peil.py" heartbeat_loop '{}' >"$STATE_DIR/heartbeat.log" 2>&1 &
HB_PID=$!
log "vite preview op $WEB_PORT"
node node_modules/vite/bin/vite.js preview --config e2e/vite.preview.config.ts --port "$WEB_PORT" </dev/null >"$STATE_DIR/web.log" 2>&1 &
WEB_PID=$!
wait_http "$E2E_BASE_URL/" 30; wait_http "$E2E_BASE_URL/api/v1/health" 10
cd "$HERE"
nice -n 10 node "$HERE/${PEIL_SCRIPT:-shots.mjs}"
