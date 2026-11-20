#!/usr/bin/env bash
# Een nooit gepubliceerde tag is optioneel. Toegangs- en verbindingsfouten blijven fouten.
set -euo pipefail
log=$(mktemp "$RUNNER_TEMP/pull.XXXXXXXX")
trap 'rm -f "$log"' EXIT
if docker pull "$1" > "$log" 2>&1; then
  bash .forgejo/ci/scan.sh "$1"
elif grep -Eqi 'manifest unknown|manifest_unknown|manifest for .* not found' "$log" || grep -Fq -- "$1: not found" "$log"; then
  echo "::notice::Tag $1 is nog niet gepubliceerd."
else
  cat "$log" >&2
  exit 1
fi
