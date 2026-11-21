#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$TMPDIR"
case $1 in
  allinone)
    image="my_house-aio:$SHC_VERSION"
    # Vergelijk een eigen gegevensvolume met twee tijdelijke bestandssystemen onder de app-identiteit.
    docker run --rm --network none --user 568:568 --mount type=volume,dst=/appdata \
      --tmpfs /etc/mosquitto:uid=568,gid=568,mode=0700 --entrypoint sh "$image" -ec '
      umask 077
      id
      for basis in /appdata /dev/shm /etc/mosquitto; do
        map=$(mktemp -d "$basis/broker.XXXXXX")
        bestand=$map/mosquitto.conf
        printf "onbekende_proefoptie\n" > "$bestand"
        ls -ldn "$map" "$bestand"
        cat "$bestand" >/dev/null
        uitvoer=$(mosquitto -c "$bestand" 2>&1) && exit 1
        case "$uitvoer" in
          *"Unknown configuration variable"*) echo "$basis: broker heeft de proefoptie gelezen" ;;
          *"Unable to open config file"*) echo "$basis: broker kan het bestand niet openen" ;;
          *) echo "$basis: onverwachte fout bij openen van proefconfiguratie"; exit 1 ;;
        esac
      done'
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
# Alleen gegevens en de bestanden die Compose bind-mount worden met de bouwdaemon gedeeld.
# De scripts, Chromium en native npm-pakketten draaien vanuit het containerbestandssysteem.
cp --parents infra/compose/compose.yaml infra/e2e/compose.e2e.yaml infra/e2e/fake_gree_unit.py \
  integrations/tests/fake_gree.py "$SHC_PROEFVOLUME"
export SHC_IMAGE_PREFIX=shc SHC_ROOT="$SHC_PROEFVOLUME/rook" SHC_E2E_ROOT="$SHC_PROEFVOLUME/browser"
export COMPOSE_FILE="$SHC_PROEFVOLUME/infra/compose/compose.yaml"
export COMPOSE_PROJECT_NAME="my-house-$SHC_VERSION" SHC_E2E_PROJECT_NAME="my-house-e2e-$SHC_VERSION"
infra/scripts/install.sh "$SHC_ROOT"
SHC_SMOKE_PASSPHRASE=$(openssl rand -base64 30)
export SHC_SMOKE_PASSPHRASE
opruimen() {
  if (( $? != 0 )); then docker compose ps -a; docker compose logs --no-color --tail 200; fi
  docker compose down -v --remove-orphans
}
trap opruimen EXIT
# Controleer de oorspronkelijke koppeling met dezelfde UID en rechten als de dienst.
docker compose run --rm -T --no-deps --entrypoint sh mosquitto -c '
  id
  ls -ldn /mosquitto/config /mosquitto/config/mosquitto.conf
  if cat /mosquitto/config/mosquitto.conf >/dev/null; then echo "Configuratie leesbaar via de koppeling"; fi'
export COMPOSE_FILE="$COMPOSE_FILE:$PWD/.forgejo/ci/broker.compose.yaml"
export SHC_E2E_COMPOSE_FILE="$COMPOSE_FILE:$SHC_PROEFVOLUME/infra/e2e/compose.e2e.yaml"
docker compose run --rm -T --no-deps --entrypoint sh mosquitto -ec '
  id
  ls -ldn /mosquitto/config /mosquitto/config/mosquitto.conf
  cat /mosquitto/config/mosquitto.conf /mosquitto/config/acl /mosquitto/config/passwd >/dev/null'
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
infra/scripts/install.sh "$SHC_E2E_ROOT"
infra/scripts/e2e_compose.sh
