#!/usr/bin/env bash
set -euo pipefail
case $1 in
  allinone)
    image="my_house-aio:$SHC_VERSION"
    python3 infra/tests/allinone_container.py "$image"
    begintijd=$(stat -c %y docs/testlog.md)
    status=0
    python3 infra/tests/allinone_herstelproef.py "$image" || status=$?
    [[ $(stat -c %y docs/testlog.md) != "$begintijd" ]] || exit 1
    cp docs/testlog.md herstelproef.md
    exit "$status"
    ;;
  stack) ;;
  *) exit 2 ;;
esac
export SHC_IMAGE_PREFIX=shc SHC_ROOT="$PWD/proefdata" COMPOSE_FILE="$PWD/infra/compose/compose.yaml"
export COMPOSE_PROJECT_NAME="my-house-$SHC_VERSION" SHC_E2E_PROJECT_NAME="my-house-e2e-$SHC_VERSION"
infra/scripts/install.sh "$SHC_ROOT"
SHC_SMOKE_PASSPHRASE=$(openssl rand -base64 30)
export SHC_SMOKE_PASSPHRASE
opruimen() {
  if (( $? != 0 )); then docker compose ps -a; docker compose logs --no-color --tail 200; fi
  docker compose down -v --remove-orphans
}
trap opruimen EXIT
docker compose up -d --wait --wait-timeout 300
python3 infra/scripts/smoke_test.py --base http://localhost:8088 \
  --edge-network "${COMPOSE_PROJECT_NAME}_edge" --client-image "shc-api:$SHC_VERSION" --backup-file-out "$TMPDIR/backupnaam"
printf '%s\n' "$SHC_SMOKE_PASSPHRASE" | docker compose exec -T api shc-api verify-backup "/backups/$(cat "$TMPDIR/backupnaam")" --passphrase-stdin
docker compose stop api
python3 infra/scripts/smoke_test.py --base http://localhost:8088 --expect-unavailable
docker compose start api
docker compose up -d --wait --wait-timeout 120
docker compose down -v --remove-orphans
trap - EXIT
(cd apps/web && npm ci --no-audit --no-fund && node node_modules/@playwright/test/cli.js install chromium)
infra/scripts/e2e_compose.sh
