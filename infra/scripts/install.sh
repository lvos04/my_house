#!/usr/bin/env bash
# One-time preparation of the data directory (TrueNAS dataset or any Linux path).
# Creates folders with the right owners, random secrets and broker/Zigbee config.
# Safe to re-run: existing secrets and configs are never overwritten.
set -euo pipefail

ROOT="${1:-${SHC_ROOT:-}}"
ADAPTER="${SHC_ZIGBEE_ADAPTER:-ember}"
if [[ -z "$ROOT" ]]; then
  echo "Gebruik: install.sh /mnt/<pool>/apps/smart-home   (of zet SHC_ROOT)" >&2
  exit 1
fi
HERE="$(cd "$(dirname "$0")/.." && pwd)"
umask 077
mkdir -p "$ROOT"/{secrets,postgres,app,backups,mosquitto/config,mosquitto/data,zigbee2mqtt}

gen() { # file
  if [[ ! -s "$1" ]]; then
    python3 -c 'import secrets;print(secrets.token_urlsafe(48))' > "$1"
    echo "aangemaakt: $1"
  fi
}
gen "$ROOT/secrets/db_password"
gen "$ROOT/secrets/secret_key"
gen "$ROOT/secrets/mqtt_shc_password"
gen "$ROOT/secrets/mqtt_z2m_password"

cp -n "$HERE/compose/config/mosquitto.conf" "$ROOT/mosquitto/config/mosquitto.conf"
cp -n "$HERE/compose/config/acl" "$ROOT/mosquitto/config/acl"
if [[ ! -s "$ROOT/mosquitto/config/passwd" ]]; then
  # Hash the passwords with the broker's own tool (needs Docker; no extra install).
  docker run --rm -v "$ROOT/mosquitto/config:/c" -v "$ROOT/secrets:/s:ro" eclipse-mosquitto:2.0.20 sh -c \
    'mosquitto_passwd -b -c /c/passwd shc "$(cat /s/mqtt_shc_password)" && mosquitto_passwd -b /c/passwd zigbee2mqtt "$(cat /s/mqtt_z2m_password)"'
  echo "aangemaakt: mosquitto wachtwoordbestand"
fi

if [[ ! -s "$ROOT/zigbee2mqtt/configuration.yaml" ]]; then
  sed "s/__ADAPTER__/$ADAPTER/" "$HERE/compose/config/zigbee2mqtt.yaml.template" > "$ROOT/zigbee2mqtt/configuration.yaml"
  printf 'mqtt_password: "%s"\n' "$(cat "$ROOT/secrets/mqtt_z2m_password")" > "$ROOT/zigbee2mqtt/secret.yaml"
  echo "aangemaakt: zigbee2mqtt configuratie (adapter: $ADAPTER)"
fi

# Owners: postgres image fixes its own dir; app=10001, mosquitto=1883, zigbee2mqtt=1000 (image default root→ok)
chown -R 10001:10001 "$ROOT/app" "$ROOT/backups"
chown -R 1883:1883 "$ROOT/mosquitto"
chmod 0444 "$ROOT"/secrets/*
chmod 0755 "$ROOT" "$ROOT/secrets"

echo
echo "Klaar. Volgende stap:"
echo "  Linux:   SHC_ROOT=$ROOT docker compose -f $HERE/compose/compose.yaml up -d"
echo "  TrueNAS: python3 $HERE/scripts/render_truenas.py --root $ROOT > smart-home.yaml"
echo "           en plak de inhoud in Apps › Discover › ⋮ › Install via YAML."
echo
echo "Zigbee-dongle:   ls -l /dev/serial/by-id/   → zet het pad in SHC_ZIGBEE_DEVICE"
