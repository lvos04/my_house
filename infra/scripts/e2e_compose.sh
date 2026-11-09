#!/usr/bin/env bash
# Browser tests (Playwright) against the compose stack (verbeterlijst 42): the real images behind nginx,
# with the API, the worker, PostgreSQL and mosquitto, plus a fake Gree airco on a small test network
# (infra/e2e/compose.e2e.yaml). Runs a smoke subset of apps/web/e2e through nginx (WebSocket proxying,
# nginx's answers while the API is stopped) and infra/e2e/worker-command.e2e.ts (a command from the
# browser executed by the worker and confirmed by the unit). apps/web/e2e/run-stack.sh runs all flows
# faster without nginx and worker; this script is the check that the deployed stack does the same.
#
# Usage (from anywhere):
#   infra/scripts/e2e_compose.sh [playwright test arguments...]      e.g.  --trace off
#
# Needs: docker with compose, the images ${SHC_IMAGE_PREFIX}-api/-web:${SHC_VERSION} (CI builds them
# as shc-*:ci; otherwise they are pulled), `npm ci` in apps/web and Chromium for Playwright
# (npx playwright install chromium), and root or sudo for install.sh.
#
# Environment:
#   SHC_IMAGE_PREFIX, SHC_VERSION  which images (as in compose.yaml)
#   SHC_E2E_FLOWS       apps/web/e2e flows after the first start, by file name prefix, or "all"
#                       (default "07-notifications 08-offline"). nginx allows 10 logins per minute
#                       (burst 5) and every flow logs in two or three times: run more flows in
#                       separate, smaller runs, or they get "429 Te veel pogingen" by design.
#   SHC_E2E_ROOT        data folder to use; it must be fresh (install.sh, never started), because the
#                       tests begin at the first-start screen. Default: a new one in a temp folder.
#   SHC_E2E_HTTP_PORT   host port for nginx (default 8089), SHC_E2E_NET_PREFIX the stack's subnets
#                       (default 172.29) and SHC_E2E_LAN_PREFIX the test network (default 192.168.77):
#                       a separate compose project (shc-e2e), so an installation on the same host is
#                       left alone.
#   SHC_E2E_KEEP        1 = leave the stack and its data folder running afterwards
#
# Internal subcommands, used by the offline flow (apps/web/e2e/helpers.ts → E2E_RUN_STACK) to stop and
# start the API container of the running stack: e2e_compose.sh api-stop | api-start
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SELF="$REPO/infra/scripts/e2e_compose.sh"
OUT="$REPO/infra/e2e/.output"

log() { printf '[e2e-compose] %s\n' "$*" >&2; }

wait_http() { # url seconds what
  local url="$1" limit="$2" what="$3" i
  for ((i = 0; i < limit; i++)); do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then return 0; fi
    sleep 1
  done
  log "$what antwoordt niet op $url"
  return 1
}

case "${1:-}" in
  api-stop)
    : "${COMPOSE_FILE:?}" "${COMPOSE_PROJECT_NAME:?}"
    docker compose stop --timeout 10 api >&2
    exit 0
    ;;
  api-start)
    : "${COMPOSE_FILE:?}" "${COMPOSE_PROJECT_NAME:?}" "${E2E_BASE_URL:?}"
    docker compose start api >&2
    wait_http "$E2E_BASE_URL/api/v1/health" 90 "De API (via nginx)"
    exit 0
    ;;
esac

SUDO=()
[[ "$(id -u)" -eq 0 ]] || SUDO=(sudo)
WEB="$REPO/apps/web"
[[ -x "$WEB/node_modules/.bin/playwright" ]] || { log "Eerst: (cd apps/web && npm ci && npx playwright install chromium)"; exit 2; }

export COMPOSE_PROJECT_NAME=shc-e2e
export COMPOSE_FILE="$REPO/infra/compose/compose.yaml:$REPO/infra/e2e/compose.e2e.yaml"
export COMPOSE_PATH_SEPARATOR=:
export SHC_NET_PREFIX="${SHC_E2E_NET_PREFIX:-172.29}"
export SHC_E2E_LAN_PREFIX="${SHC_E2E_LAN_PREFIX:-192.168.77}"
export SHC_HTTP_PORT="${SHC_E2E_HTTP_PORT:-8089}"
export SHC_LAN_URL="" SHC_REMOTE_URL=""
export E2E_BASE_URL="http://127.0.0.1:$SHC_HTTP_PORT"
export E2E_RUN_STACK="$SELF"
export E2E_FAKE_GREE_HOST="$SHC_E2E_LAN_PREFIX.10"
unset E2E_API_URL

STATE="$(mktemp -d "${TMPDIR:-/tmp}/shc-e2e-compose.XXXXXX")"
OWN_ROOT=0
if [[ -n "${SHC_E2E_ROOT:-}" ]]; then
  export SHC_ROOT="$SHC_E2E_ROOT"
else
  export SHC_ROOT="$STATE/root"
  OWN_ROOT=1
fi

# Test data (apps/web/e2e/seed.py) goes in through the API container: same code, same database and
# settings as the API under test. helpers.ts runs "$E2E_PYTHON <seed.py> <command> <json>".
cat >"$STATE/python" <<'EOF'
#!/usr/bin/env bash
script="$1"; shift
exec docker compose exec -T api python - "$@" <"$script"
EOF
chmod 0755 "$STATE/python"
export E2E_PYTHON="$STATE/python"

STATUS=1
# shellcheck disable=SC2329 # called by the EXIT trap
cleanup() {
  set +e
  if [[ "$STATUS" != 0 ]]; then
    mkdir -p "$OUT"
    docker compose ps -a >"$OUT/compose-ps.txt" 2>&1
    docker compose logs --no-color --timestamps >"$OUT/compose.log" 2>&1
    log "Mislukt. Logboeken van de stack: $OUT/compose.log. Laatste regels van api, worker en fake-gree:"
    docker compose logs --no-color --tail 40 api worker fake-gree >&2 2>&1
  fi
  if [[ "${SHC_E2E_KEEP:-0}" == 1 ]]; then
    log "Bewaard: project $COMPOSE_PROJECT_NAME op $E2E_BASE_URL, data in $SHC_ROOT"
    log "Opruimen: COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME COMPOSE_FILE=$COMPOSE_FILE SHC_ROOT=$SHC_ROOT docker compose down -v"
  else
    docker compose down -v --remove-orphans >/dev/null 2>&1
    [[ "$OWN_ROOT" == 1 ]] && "${SUDO[@]}" rm -rf "$SHC_ROOT"
    rm -rf "$STATE"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

log "Typecheck van infra/e2e"
"$WEB/node_modules/.bin/tsc" -p "$REPO/infra/e2e/tsconfig.json"

if [[ "$OWN_ROOT" == 1 ]]; then
  log "Nieuwe datamap (install.sh): $SHC_ROOT"
  "${SUDO[@]}" "$REPO/infra/scripts/install.sh" "$SHC_ROOT" >"$STATE/install.log" 2>&1 \
    || { cat "$STATE/install.log" >&2; exit 1; }
fi

log "Stack starten (project $COMPOSE_PROJECT_NAME, nginx op $E2E_BASE_URL)"
docker compose up -d --wait --wait-timeout 300
wait_http "$E2E_BASE_URL/api/v1/health" 60 "De API (via nginx)"

log "Playwright starten"
cd "$WEB"
set +e
node_modules/.bin/playwright test --config "$REPO/infra/e2e/playwright.config.ts" "$@"
STATUS=$?
set -e
exit "$STATUS"
