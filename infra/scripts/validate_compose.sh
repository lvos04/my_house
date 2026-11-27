#!/usr/bin/env bash
# Validate infra/compose/compose.yaml for every profile combination and every TrueNAS render
# (render_truenas.py), with throw-away dummy secrets. Used by CI; safe to run anywhere with docker.
# The sh -c '...' snippets get their arguments as $1.. on purpose (no expansion here).
# shellcheck disable=SC2016
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$HERE/compose/compose.yaml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ROOT="$WORK/root"
mkdir -p "$ROOT/secrets"
for s in db_password secret_key mqtt_shc_password mqtt_z2m_password tunnel_token; do
  printf 'dummy-%s\n' "$s" > "$ROOT/secrets/$s"
done
chmod 0400 "$ROOT"/secrets/*

fail=0
check() { # description, command...
  local what="$1"; shift
  if "$@" >"$WORK/out" 2>&1; then
    echo "ok    $what"
  else
    echo "FOUT  $what"; sed 's/^/      /' "$WORK/out"; fail=1
  fi
}

for profiles in "" "zigbee" "tunnel" "zigbee,tunnel"; do
  args=()
  IFS=',' read -ra list <<< "$profiles"
  for p in "${list[@]}"; do args+=(--profile "$p"); done
  check "compose.yaml [${profiles:-basis}]" \
    env SHC_ROOT="$ROOT" SHC_ZIGBEE_DEVICE=/dev/null docker compose -f "$COMPOSE" "${args[@]}" config -q

  out="$WORK/truenas-${profiles:-basis}.yaml"
  render=(python3 "$HERE/scripts/render_truenas.py" --root "$ROOT" --lan-url http://woning.test:8088)
  [[ -n "$profiles" ]] && render+=(--with "$profiles")
  [[ "$profiles" == *zigbee* ]] && render+=(--zigbee-device /dev/null)
  [[ "$profiles" == *tunnel* ]] && render+=(--remote-url https://huis.example.nl)
  check "render_truenas.py [${profiles:-basis}]" sh -c '"$@" > "'"$out"'"' _ "${render[@]}"
  check "TrueNAS-YAML is geldige compose [${profiles:-basis}]" docker compose -f "$out" config -q
  check "TrueNAS-YAML zonder variabelen of profielen [${profiles:-basis}]" \
    sh -c '! grep -nE "\\$\\{|profiles:" "$1"' _ "$out"
  check "TrueNAS-YAML bevat geen geheimen [${profiles:-basis}]" \
    sh -c '! grep -n "dummy-" "$1"' _ "$out"
  if [[ "$profiles" == *tunnel* ]]; then
    check "tunneltoken als bestand [${profiles}]" grep -q -- "--token-file" "$out"
  else
    check "geen tunnel zonder tunnel [${profiles:-basis}]" sh -c '! grep -v "^ *#" "$1" | grep -nE "tunnel_token|cloudflared"' _ "$out"
  fi
done

# The test-only additions for the browser tests (infra/e2e/compose.e2e.yaml) still merge cleanly.
check "compose.yaml + infra/e2e/compose.e2e.yaml" \
  env SHC_ROOT="$ROOT" docker compose -f "$COMPOSE" -f "$HERE/e2e/compose.e2e.yaml" config -q
check "ontwikkelstandaarden gepind, E2E gebruikt dezelfde API-image" bash -o pipefail -c \
  'env -u SHC_IMAGE_PREFIX -u SHC_VERSION SHC_ROOT="$1" SHC_ZIGBEE_DEVICE=/dev/null \
     docker compose --env-file /dev/null --profile "*" -f "$2" -f "$3" config --format json \
     | python3 -c "$4"' \
  _ "$ROOT" "$COMPOSE" "$HERE/e2e/compose.e2e.yaml" \
  'import json, re, sys
s = json.load(sys.stdin)["services"]
assert all(re.search(r"@sha256:[0-9a-f]{64}$", service["image"]) for service in s.values())
assert s["api"]["image"] == s["worker"]["image"] == s["fake-gree"]["image"]'
check "CI-overschrijvingen kiezen de lokaal gebouwde API en web" bash -o pipefail -c \
  'SHC_ROOT="$1" SHC_IMAGE_PREFIX=shc SHC_VERSION=ci \
     docker compose -f "$2" -f "$3" config --format json | python3 -c "$4"' \
  _ "$ROOT" "$COMPOSE" "$HERE/e2e/compose.e2e.yaml" \
  'import json, sys
s = json.load(sys.stdin)["services"]
assert all(s[name]["image"] == "shc-api:ci" for name in ("api", "worker", "fake-gree"))
assert s["web"]["image"] == "shc-web:ci"'

render_images() {
  docker compose --env-file /dev/null -f "$1" config --format json |
    python3 -c 'import json, sys
services = json.load(sys.stdin)["services"]
for name in ("api", "worker", "web"):
    print(services[name]["image"])'
}
source_images=$(unset SHC_VERSION SHC_IMAGE_PREFIX; SHC_ROOT="$ROOT" render_images "$COMPOSE")
rendered_images=$(render_images "$WORK/truenas-basis.yaml")
check "standaardrender gebruikt per dienst dezelfde gepinde image als Compose" test "$rendered_images" = "$source_images"
python3 "$HERE/scripts/render_truenas.py" --root "$ROOT" --image-prefix shc --version ci > "$WORK/ci.yaml"
ci_images=$(render_images "$WORK/ci.yaml")
check "render met expliciete CI-versie gebruikt de lokale API, worker en web" test "$ci_images" = $'shc-api:ci\nshc-api:ci\nshc-web:ci'

# The push-address fallback (verbeterlijst 28) reaches api and worker from the environment; the
# TrueNAS render leaves it empty (set it in the app there: it is stored encrypted, not in the YAML).
check "SHC_NOTIFY_URL naar api en worker" sh -c \
  'SHC_ROOT="$1" SHC_NOTIFY_URL="$3" docker compose -f "$2" config --format json | python3 -c "$4" "$3"' \
  _ "$ROOT" "$COMPOSE" https://ntfy.example.nl/shc-test \
  'import json, sys; s = json.load(sys.stdin)["services"]
sys.exit(any(s[n]["environment"].get("SHC_NOTIFY_URL") != sys.argv[1] for n in ("api", "worker")))'
check "TrueNAS-YAML laat SHC_NOTIFY_URL leeg" grep -q 'SHC_NOTIFY_URL: ""' "$WORK/truenas-basis.yaml"
# The cookie must not be forced to Secure (verbeterlijst 3): auto unless the user overrides it.
check "cookie Secure staat op auto" grep -q "SHC_COOKIE_SECURE: auto" "$WORK/truenas-basis.yaml"
# The reference clock for the time check (verbeterlijst 28) reaches api and worker: pool.ntp.org by
# default, a router/NAS when set; render_truenas.py --ntp-server does the same for TrueNAS.
ntp_py='import json, sys; s = json.load(sys.stdin)["services"]
sys.exit(any(s[n]["environment"].get("SHC_NTP_SERVER") != sys.argv[1] for n in ("api", "worker")))'
check "SHC_NTP_SERVER standaard pool.ntp.org naar api en worker" sh -c \
  'SHC_ROOT="$1" docker compose -f "$2" config --format json | python3 -c "$3" pool.ntp.org' \
  _ "$ROOT" "$COMPOSE" "$ntp_py"
ntp_proef=$(printf '%d.%d.%d.%d' 10 83 29 1)
check "SHC_NTP_SERVER eigen tijdserver naar api en worker" sh -c \
  'SHC_ROOT="$1" SHC_NTP_SERVER="$4" docker compose -f "$2" config --format json | python3 -c "$3" "$4"' \
  _ "$ROOT" "$COMPOSE" "$ntp_py" "$ntp_proef"
check "TrueNAS-YAML: tijdserver standaard pool.ntp.org" grep -q 'SHC_NTP_SERVER: "pool.ntp.org"' "$WORK/truenas-basis.yaml"
check "render met lokaal NTP-proefadres en poort" sh -c \
  'python3 "$1" --root "$2" --ntp-server "$4:123" > "$3" && grep -q "SHC_NTP_SERVER: \"$4:123\"" "$3" && docker compose -f "$3" config -q' \
  _ "$HERE/scripts/render_truenas.py" "$ROOT" "$WORK/ntp.yaml" "$ntp_proef"
check "render met --ntp-server \"\" zet de klokcontrole uit" sh -c \
  'python3 "$1" --root "$2" --ntp-server "" > "$3" && grep -q "SHC_NTP_SERVER: \"\"" "$3"' \
  _ "$HERE/scripts/render_truenas.py" "$ROOT" "$WORK/ntp-off.yaml"
check "render weigert een ongeldige tijdserver" sh -c \
  '! python3 "$1" --root "$2" --ntp-server "ntp server\"" > /dev/null 2>&1' \
  _ "$HERE/scripts/render_truenas.py" "$ROOT"
# A different internal address range moves every fixed address with it.
proefprefix=10.77
check "render met --net-prefix $proefprefix" sh -c \
  'python3 "$1" --root "$2" --net-prefix "$4" > "$3" && grep -Fq "$4.251.2" "$3" && ! grep -v "^ *#" "$3" | grep -q "172\.30\."' \
  _ "$HERE/scripts/render_truenas.py" "$ROOT" "$WORK/prefix.yaml" "$proefprefix"

if (( fail )); then
  echo "Afwijking gevonden in de Compose-controles."
else
  echo "Alle Compose-controles akkoord."
fi
exit "$fail"
