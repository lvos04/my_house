#!/usr/bin/env bash
# Dit bestand wordt gesourcet. De configuratie bevat alleen de naam van de credentialhelper.
export PATH="$PWD/.forgejo/ci:$PATH" DOCKER_CONFIG="$RUNNER_TEMP/shc-dockerconfig"
mkdir -p "$DOCKER_CONFIG"
printf '%s\n' '{"credHelpers":{"git.medialeon.nl":"forgejo-ci"}}' > "$DOCKER_CONFIG/config.json"
