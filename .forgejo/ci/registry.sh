#!/usr/bin/env bash
# De checkout kan noexec zijn. Docker start zijn helper vanuit de privémap van deze job.
export SHC_REGISTRY_HOST=${1:-git.medialeon.nl}
case $SHC_REGISTRY_HOST in git.medialeon.nl|ghcr.io) ;; *) return 1 ;; esac
DOCKER_CONFIG=$(mktemp -d "$RUNNER_TEMP/shc-dockerconfig.XXXXXXXX")
install -m 755 .forgejo/ci/docker-credential-shc-ci "$DOCKER_CONFIG/docker-credential-shc-ci"
export DOCKER_CONFIG PATH="$DOCKER_CONFIG:$PATH"
jq -n --arg host "$SHC_REGISTRY_HOST" '{credHelpers:{($host):"shc-ci"}}' > "$DOCKER_CONFIG/config.json"
