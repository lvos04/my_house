#!/usr/bin/env bash
# Ook de historische upgradeproeven starten zelf een PostgreSQL 16. initdb vereist een gewone gebruiker.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
pgbin=/usr/lib/postgresql/16/bin
if [[ ! -x $pgbin/initdb ]]; then
  apt-get install -y --no-install-recommends postgresql-common
  echo 'create_main_cluster = false' >> /etc/postgresql-common/createcluster.conf
  apt-get install -y --no-install-recommends postgresql-16
fi
export PATH="$pgbin:$PATH"
basis=$(mktemp -d "$RUNNER_TEMP/shc-pg.XXXXXXXX")
chown postgres:postgres "$basis"
install -d -o postgres -g postgres -m 700 "$basis/tmp"
as_pg=(runuser -u postgres --)
opruimen() {
  if [[ -f $basis/data/postmaster.pid ]]; then
    "${as_pg[@]}" pg_ctl -D "$basis/data" -m fast -w stop
  fi
  rm -rf -- "$basis"
}
trap opruimen EXIT
"${as_pg[@]}" initdb -D "$basis/data" --auth-local=trust --auth-host=reject --locale=C.UTF-8
"${as_pg[@]}" pg_ctl -D "$basis/data" -l "$basis/server.log" -o "-c listen_addresses='' -k '$basis'" -w start
export PGHOST="$basis" SHC_TEST_PG=postgresql://postgres@ SHC_E2E_PG=postgresql://postgres@
export TMPDIR="$basis/tmp"
case $1 in
  backend)
    # Alleen deze checkout vertrouwen wanneer postgres de historische commits uitleest.
    export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"
    cd apps/api
    "${as_pg[@]}" env PATH="$PATH" python -m pytest -p no:cacheprovider -q tests
    ;;
  e2e)
    SHC_E2E_PYTHON=python apps/web/e2e/run-stack.sh
    ;;
  *) exit 2 ;;
esac
