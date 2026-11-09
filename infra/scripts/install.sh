#!/usr/bin/env bash
# Prepare the data directory (TrueNAS dataset or any Linux path): folders with the right owners,
# random secrets, broker and Zigbee2MQTT configuration.
#
# Safe to re-run, and re-run it after every update: existing secrets, the broker password file and
# the Zigbee2MQTT configuration are never overwritten; owners and permissions are (re)applied and
# the broker's config/acl are refreshed (a changed copy is kept as .bak-<time>).
#
# Who reads what (uids of the containers):
#   70     postgres     postgres/ (0700), secrets/db_password
#   10001  api, worker  app/, backups/, secrets/{secret_key,mqtt_shc_password,db_password}, zigbee2mqtt/ (read)
#   1883   mosquitto    mosquitto/
#   1000   zigbee2mqtt  zigbee2mqtt/
#   65532  cloudflared  secrets/tunnel_token
set -euo pipefail

ROOT="${1:-${SHC_ROOT:-}}"
ADAPTER="${SHC_ZIGBEE_ADAPTER:-ember}"
APP_UID=10001
PG_UID=70
MQTT_UID=1883
Z2M_UID=1000
CF_UID=65532

die() { echo "FOUT: $*" >&2; exit 1; }
warn() { echo "LET OP: $*" >&2; }

if [[ -z "$ROOT" ]]; then
  die "Gebruik: install.sh /mnt/<pool>/apps/smart-home   (of zet SHC_ROOT)"
fi
[[ "$(id -u)" -eq 0 ]] || die "Voer dit uit als root (sudo): het script zet eigenaars en rechten van mappen."
case "$ADAPTER" in ember|zstack|deconz|zigate|zboss) ;; *) die "Onbekende SHC_ZIGBEE_ADAPTER '$ADAPTER' (ember, zstack, deconz, zigate of zboss)." ;; esac

HERE="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$HERE/compose/compose.yaml"
# The broker image comes from compose.yaml, so there is only one place to update it.
MOSQUITTO_IMAGE="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(eclipse-mosquitto:[^[:space:]]*\).*/\1/p' "$COMPOSE" | head -n1)"
[[ -n "$MOSQUITTO_IMAGE" ]] || die "Kan het mosquitto-image niet vinden in $COMPOSE."

ACL_HINT="Waarschijnlijk gebruikt deze dataset ACL's (TrueNAS-preset 'Apps' of 'SMB'). Maak de dataset met preset 'Generic', of zet de rechten via Datasets › Edit Permissions zodat gewone Unix-rechten gelden. Zie docs/installatie-truenas.md."

# own <uid:gid> <mode> <path...>: owner and mode, with a clear message when the dataset refuses it.
own() {
  local who="$1" mode="$2"; shift 2
  chown "$who" "$@" 2>/dev/null || die "Eigenaar $who zetten op $* lukt niet. $ACL_HINT"
  chmod "$mode" "$@" 2>/dev/null || die "Rechten $mode zetten op $* lukt niet. $ACL_HINT"
}

# expect <mode> <path>: verify the mode really stuck (ACL datasets can silently change it).
expect() {
  local got
  got="$(stat -c '%a' "$2")"
  [[ "$got" == "$1" ]] || warn "$2 heeft rechten $got in plaats van $1. $ACL_HINT"
}

umask 077
mkdir -p "$ROOT"
chmod 0755 "$ROOT"
for d in secrets postgres app backups mosquitto mosquitto/config mosquitto/data zigbee2mqtt; do
  mkdir -p "$ROOT/$d"
done

# --- secrets -------------------------------------------------------------------------------------
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

own 0:0 0700 "$ROOT/secrets"
# db_password: read by postgres (owner) and by api/worker (group 10001).
own "$PG_UID:$APP_UID" 0440 "$ROOT/secrets/db_password"
own "$APP_UID:$APP_UID" 0400 "$ROOT/secrets/secret_key" "$ROOT/secrets/mqtt_shc_password"
# Only used here, to fill the broker password file and Zigbee2MQTT's secret.yaml.
own 0:0 0400 "$ROOT/secrets/mqtt_z2m_password"
if [[ -e "$ROOT/secrets/tunnel_token" ]]; then
  [[ -s "$ROOT/secrets/tunnel_token" ]] || die "$ROOT/secrets/tunnel_token is leeg. Plak het tunneltoken erin (zie docs/buiten-huis.md)."
  own "$CF_UID:$CF_UID" 0400 "$ROOT/secrets/tunnel_token"
fi
expect 700 "$ROOT/secrets"
expect 400 "$ROOT/secrets/secret_key"

# --- app data ------------------------------------------------------------------------------------
chown -R "$APP_UID:$APP_UID" "$ROOT/app" "$ROOT/backups"
own "$APP_UID:$APP_UID" 0750 "$ROOT/app" "$ROOT/backups"

# PostgreSQL runs as uid 70 and refuses a data folder it does not own (or that others can read).
if [[ "$(stat -c '%u' "$ROOT/postgres")" != "$PG_UID" ]] || [[ -n "$(find "$ROOT/postgres" -mindepth 1 -maxdepth 1 ! -user "$PG_UID" -print -quit)" ]]; then
  chown -R "$PG_UID:$PG_UID" "$ROOT/postgres" 2>/dev/null || die "Eigenaar van $ROOT/postgres zetten lukt niet. $ACL_HINT"
fi
own "$PG_UID:$PG_UID" 0700 "$ROOT/postgres"
expect 700 "$ROOT/postgres"

# --- broker ----------------------------------------------------------------------------------------
# config and acl belong to the app: install them, or replace a changed copy (kept as .bak-<time>).
install_managed() { # src dest
  if [[ ! -e "$2" ]]; then
    cp "$1" "$2"
    echo "aangemaakt: $2"
  elif ! cmp -s "$1" "$2"; then
    local bak
    bak="$2.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$2" "$bak"
    cp "$1" "$2"
    echo "bijgewerkt: $2 (oude versie: $bak)"
  fi
}
install_managed "$HERE/compose/config/mosquitto.conf" "$ROOT/mosquitto/config/mosquitto.conf"
install_managed "$HERE/compose/config/acl" "$ROOT/mosquitto/config/acl"
if [[ ! -s "$ROOT/mosquitto/config/passwd" ]]; then
  command -v docker >/dev/null || die "docker niet gevonden: nodig om het wachtwoordbestand van de broker te maken."
  # Hash the passwords with the broker's own tool (no extra install needed).
  docker run --rm --network none -v "$ROOT/mosquitto/config:/c" -v "$ROOT/secrets:/s:ro" "$MOSQUITTO_IMAGE" sh -c \
    'mosquitto_passwd -b -c /c/passwd shc "$(cat /s/mqtt_shc_password)" && mosquitto_passwd -b /c/passwd zigbee2mqtt "$(cat /s/mqtt_z2m_password)"'
  echo "aangemaakt: mosquitto wachtwoordbestand"
fi
chown -R "$MQTT_UID:$MQTT_UID" "$ROOT/mosquitto"
own "$MQTT_UID:$MQTT_UID" 0750 "$ROOT/mosquitto" "$ROOT/mosquitto/config" "$ROOT/mosquitto/data"
# The broker warns about (and newer versions refuse) world-readable password/acl files.
own "$MQTT_UID:$MQTT_UID" 0640 "$ROOT/mosquitto/config/passwd" "$ROOT/mosquitto/config/acl" "$ROOT/mosquitto/config/mosquitto.conf"
expect 640 "$ROOT/mosquitto/config/passwd"

# --- Zigbee2MQTT -----------------------------------------------------------------------------------
if [[ ! -s "$ROOT/zigbee2mqtt/configuration.yaml" ]]; then
  sed "s/__ADAPTER__/$ADAPTER/" "$HERE/compose/config/zigbee2mqtt.yaml.template" > "$ROOT/zigbee2mqtt/configuration.yaml"
  printf 'mqtt_password: "%s"\n' "$(cat "$ROOT/secrets/mqtt_z2m_password")" > "$ROOT/zigbee2mqtt/secret.yaml"
  echo "aangemaakt: zigbee2mqtt configuratie (adapter: $ADAPTER)"
fi
# Owner 1000 (Zigbee2MQTT) writes; group 10001 (api/worker) may read, so the network key and the
# device list end up in the backup (verbeterlijst 33). setgid: new files keep group 10001.
chown -R "$Z2M_UID:$APP_UID" "$ROOT/zigbee2mqtt" 2>/dev/null || die "Eigenaar van $ROOT/zigbee2mqtt zetten lukt niet. $ACL_HINT"
find "$ROOT/zigbee2mqtt" -type d -exec chmod 2750 {} + 2>/dev/null || die "Rechten in $ROOT/zigbee2mqtt zetten lukt niet. $ACL_HINT"
find "$ROOT/zigbee2mqtt" -type f -exec chmod 0640 {} + 2>/dev/null || die "Rechten in $ROOT/zigbee2mqtt zetten lukt niet. $ACL_HINT"
expect 2750 "$ROOT/zigbee2mqtt"

# --- check as the users that will read it --------------------------------------------------------
# Exactly like the stack does it: bind-mounted into a container that runs as that uid (the
# secrets folder itself stays closed to everyone but root; Docker mounts the files one by one).
can_read() { # uid:gid, then host paths; each must be readable (directories: listable)
  local who="$1"; shift
  local args=() tests=() i=0 p
  for p in "$@"; do
    args+=(-v "$p:/check/$i:ro")
    if [[ -d "$p" ]]; then
      # the folder, and everything in it, must be readable (find prints what is not)
      tests+=("test -r /check/$i -a -x /check/$i || exit 1; find /check/$i -exec test -r {} \\; -o -print 2>&1 | grep -q . && exit 1;")
    else
      tests+=("test -r /check/$i || exit 1;")
    fi
    i=$((i + 1))
  done
  docker run --rm --network none --user "$who" --entrypoint sh "${args[@]}" "$MOSQUITTO_IMAGE" -c "${tests[*]} true" >/dev/null 2>&1
}
if command -v docker >/dev/null; then
  problems=0
  can_read "$APP_UID:$APP_UID" "$ROOT/secrets/db_password" "$ROOT/secrets/secret_key" "$ROOT/secrets/mqtt_shc_password" \
    || { warn "api/worker (uid $APP_UID) kan zijn geheimen niet lezen."; problems=1; }
  can_read "$APP_UID:$APP_UID" "$ROOT/zigbee2mqtt" \
    || { warn "api/worker (uid $APP_UID) kan $ROOT/zigbee2mqtt niet (volledig) lezen: Zigbee ontbreekt dan in de back-up."; problems=1; }
  can_read "$PG_UID:$PG_UID" "$ROOT/secrets/db_password" "$ROOT/postgres" \
    || { warn "postgres (uid $PG_UID) kan secrets/db_password of postgres/ niet lezen."; problems=1; }
  can_read "$MQTT_UID:$MQTT_UID" "$ROOT/mosquitto/config" \
    || { warn "mosquitto (uid $MQTT_UID) kan mosquitto/config niet lezen."; problems=1; }
  can_read "$Z2M_UID:$Z2M_UID" "$ROOT/zigbee2mqtt" \
    || { warn "zigbee2mqtt (uid $Z2M_UID) kan $ROOT/zigbee2mqtt niet lezen."; problems=1; }
  if [[ -e "$ROOT/secrets/tunnel_token" ]]; then
    can_read "$CF_UID:$CF_UID" "$ROOT/secrets/tunnel_token" \
      || { warn "cloudflared (uid $CF_UID) kan secrets/tunnel_token niet lezen."; problems=1; }
  fi
  if (( problems )); then
    warn "Niet alle containers kunnen hun bestanden lezen. $ACL_HINT"
  else
    echo "controle: elke container kan zijn eigen bestanden lezen; de back-up kan Zigbee2MQTT lezen."
  fi
fi

echo
echo "Klaar. Volgende stap:"
echo "  Linux:   SHC_ROOT=$ROOT SHC_VERSION=\$(cat $HERE/../VERSION) docker compose -f $COMPOSE up -d"
echo "  TrueNAS: python3 $HERE/scripts/render_truenas.py --root $ROOT --lan-url http://<NAS-IP>:8088 > $ROOT/smart-home.yaml"
echo "           en plak de inhoud in Apps › Discover › ⋮ › Install via YAML."
echo
echo "Zigbee-dongle:   ls -l /dev/serial/by-id/   → geef het pad mee met --zigbee-device"
echo "Tunnel:          zet het token in $ROOT/secrets/tunnel_token en voer dit script opnieuw uit."
