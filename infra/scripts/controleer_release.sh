#!/usr/bin/env bash
# Weiger publicatie bij een bestaande release, een bezette registertag of een onzekere uitkomst.
set -euo pipefail

versie=$(cat VERSION)
if [[ $GITHUB_REF_TYPE != tag || $GITHUB_REF_NAME != "v$versie" || ! $versie =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo 'De git-tag moet exact v<VERSION> zijn met drie numerieke versiedelen.' >&2
  exit 1
fi

vereis_afwezig() {
  local naam=$1 status
  shift
  status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$@")
  case $status in
    404) ;;
    200) echo "$naam bestaat al. Publicatie gestopt." >&2; return 1 ;;
    *) echo "$naam kon niet worden gecontroleerd (HTTP $status)." >&2; return 1 ;;
  esac
}

vereis_afwezig 'GitHub-release' --header "Authorization: Bearer $GH_TOKEN" \
  "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/releases/tags/$GITHUB_REF_NAME"

pakket=${IMAGE#ghcr.io/}
toegang=$(curl --fail --silent --show-error --user "$GITHUB_ACTOR:$GH_TOKEN" --get \
  --data-urlencode 'service=ghcr.io' --data-urlencode "scope=repository:$pakket:pull,push" \
  https://ghcr.io/token | jq -er '.token | strings | select(length > 0)')
formaten='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json'
formaten+=', application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'
vereis_afwezig 'GHCR-versietag' --head --header "Authorization: Bearer $toegang" \
  --header "Accept: $formaten" "https://ghcr.io/v2/$pakket/manifests/$versie"

echo "version=$versie" >> "$GITHUB_OUTPUT"
