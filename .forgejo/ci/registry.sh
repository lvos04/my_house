#!/usr/bin/env bash
# Dit bestand wordt gesourcet. De configuratie bevat alleen de naam van de credentialhelper.
export PATH="$PWD/.forgejo/ci:$PATH" DOCKER_CONFIG="$RUNNER_TEMP/shc-dockerconfig"
export SHC_REGISTRY_HOST=${1:-git.medialeon.nl}
case $SHC_REGISTRY_HOST in git.medialeon.nl|ghcr.io) ;; *) return 1 ;; esac
mkdir -p "$DOCKER_CONFIG"
jq -n --arg host "$SHC_REGISTRY_HOST" '{credHelpers:{($host):"shc-ci"}}' > "$DOCKER_CONFIG/config.json"
