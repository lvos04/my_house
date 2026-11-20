#!/usr/bin/env bash
# De image staat op de geïsoleerde bouwdaemon. Dezelfde Trivy-versie en drempel als de bestaande scan.
set -euo pipefail
docker run --rm --volume /var/run/docker.sock:/var/run/docker.sock:ro \
  docker.io/aquasec/trivy:0.70.0@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e \
  image --image-src docker --no-progress --ignore-unfixed --severity CRITICAL --exit-code 1 --format table "$1"
