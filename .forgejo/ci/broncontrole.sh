#!/usr/bin/env bash
# Lees alleen de gepinde objecten en de bijbehorende bronbranches. Git bewaart het leessecret nergens.
set -euo pipefail
: "${BRON_LEESTOKEN:?BRON_LEESTOKEN ontbreekt}"
bron="$RUNNER_TEMP/peil-en-contract"
git init --quiet "$bron"
git -C "$bron" remote add origin https://git.medialeon.nl/leon/ai-server-manager.git
git -C "$bron" sparse-checkout set docs/platform-v2 docs/adr design/ontwerptaal
for soort in contract peil; do
  if [[ $soort == contract ]]; then pin=docs/platform-v2/CONTRACT_HERKOMST.json; else pin=design/ontwerptaal/HERKOMST.json; fi
  commit=$(jq -er .bron_commit "$pin")
  tak=$(jq -er .bron_ref "$pin")
  # shellcheck disable=SC2016 # Alleen de credentialhelper breidt de variabele uit.
  GIT_TERMINAL_PROMPT=0 git -C "$bron" -c 'credential.helper=!f() { if test "$1" = get; then printf "%s\n" "username=x-access-token" "password=$BRON_LEESTOKEN"; fi; }; f' \
    fetch --quiet --no-tags --depth=1 origin "$commit" "refs/heads/$tak:refs/remotes/origin/$tak"
  git -C "$bron" checkout --quiet --detach "origin/$tak"
  env -u BRON_LEESTOKEN python3 "tools/${soort}_kopie.py" controleer
  env -u BRON_LEESTOKEN python3 "tools/${soort}_kopie.py" vergelijk --bron "$bron"
done
