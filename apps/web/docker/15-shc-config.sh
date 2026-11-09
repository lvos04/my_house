#!/bin/sh
# Instellingen voor de web-app, gemaakt bij de start van de container (nginx-image: /docker-entrypoint.d/).
# Alleen "Terug naar de Hub" (O-11, ADR-0008 punt 3): de optionele omgevingsvariabele HUB_URL wordt na
# controle geschreven als {"hub_url": "<origin>"}; leeg, afwezig of ongeldig = {} (de app toont dan geen link).
# Regels zoals HUB_URL in contract 1.2 §3.2: http(s)://<host>[:<poort>] zonder pad, query, fragment of
# inloggegevens; geen loopback, link-local, 0.0.0.0 of ::. Een ongeldige waarde stopt de container nooit.
# nginx serveert het bestand als /_shc/config.json (nginx.conf.template). SHC_CONFIG_DIR alleen voor tests.
set -eu

dir="${SHC_CONFIG_DIR:-/tmp/shc-config}"
hub="${HUB_URL:-}"
json='{}'

ongeldig() {
  echo "15-shc-config: HUB_URL genegeerd ($1); de link 'Terug naar de Hub' blijft verborgen." >&2
}

if [ -n "$hub" ]; then
  # Alleen schema + host (naam, IPv4 of [IPv6]) + optionele poort, hooguit één afsluitende "/". Daardoor
  # kunnen er ook geen aanhalingstekens of andere tekens in de JSON komen.
  if ! printf '%s' "$hub" | grep -Eq '^https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?)(:[0-9]{1,5})?/?$'; then
    ongeldig "geen http(s)://host[:poort] zonder pad, query of inloggegevens"
  else
    origin="${hub%/}"
    rest="${origin#*://}"
    case "$rest" in
      \[*) host="${rest%%]*}]" ; port="${rest#"$host"}" ;;
      *) host="${rest%%:*}" ; port="${rest#"$host"}" ;;
    esac
    port="${port#:}"
    lower="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
    if [ -n "$port" ] && { [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; }; then
      ongeldig "poort buiten 1-65535"
    else
      case "$lower" in
        localhost | *.localhost | 127.* | 0.* | 169.254.* | '[::]' | '[::1]' | '[0:0:0:0:0:0:0:1]' | '[fe8'* | '[fe9'* | '[fea'* | '[feb'* | '[::ffff:'*)
          ongeldig "loopback of link-local adres"
          ;;
        *)
          json="{\"hub_url\":\"$origin\"}"
          ;;
      esac
    fi
  fi
fi

mkdir -p "$dir"
printf '%s\n' "$json" >"$dir/config.json.tmp"
mv -f "$dir/config.json.tmp" "$dir/config.json"
