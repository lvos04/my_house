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
# De historische proefprocessen toetsen zelf dat de URL een loopback-host bevat.
poort=$(python -c 'import socket; s=socket.socket(); s.bind(("localhost", 0)); print(s.getsockname()[1]); s.close()')
"${as_pg[@]}" initdb -D "$basis/data" --auth-local=trust --auth-host=trust --locale=C.UTF-8
"${as_pg[@]}" pg_ctl -D "$basis/data" -l "$basis/server.log" -o "-c listen_addresses=localhost -p $poort -k '$basis'" -w start
export PGHOST=localhost PGPORT="$poort" SHC_TEST_PG="postgresql://postgres@localhost:$poort" SHC_E2E_PG="postgresql://postgres@localhost:$poort"
case $1 in
  backend)
    export TMPDIR="$basis/tmp"
    # Alleen deze checkout vertrouwen wanneer postgres de historische commits uitleest.
    export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"
    cd apps/api
    "${as_pg[@]}" env PATH="$PATH" python -m pytest -p no:cacheprovider -q tests
    ;;
  e2e)
    TMPDIR="$RUNNER_TEMP" DEBUG=pw:browser SHC_E2E_PYTHON=python apps/web/e2e/run-stack.sh
    ;;
  *) exit 2 ;;
esac
