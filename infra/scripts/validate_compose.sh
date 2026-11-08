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
  render=(python3 "$HERE/scripts/render_truenas.py" --root "$ROOT" --lan-url http://192.168.1.20:8088)
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

# The cookie must not be forced to Secure (verbeterlijst 3): auto unless the user overrides it.
check "cookie Secure staat op auto" grep -q "SHC_COOKIE_SECURE: auto" "$WORK/truenas-basis.yaml"
# A different internal address range moves every fixed address with it.
check "render met --net-prefix 10.77" sh -c \
  'python3 "$1" --root "$2" --net-prefix 10.77 > "$3" && grep -q "10.77.251.2" "$3" && ! grep -v "^ *#" "$3" | grep -q "172\.30\."' \
  _ "$HERE/scripts/render_truenas.py" "$ROOT" "$WORK/prefix.yaml"

exit "$fail"
