#!/usr/bin/env bash
# De Linux-x64-hash komt uit de gepinde upstream-installatieactie (756fe2d284c7292c4e9b62c8a28f1e5629c59532).
set -euo pipefail
map=$(mktemp -d "$RUNNER_TEMP/cosign-bin.XXXXXXXX")
curl -fsSL --retry 3 --retry-all-errors -o "$map/cosign" \
  https://github.com/sigstore/cosign/releases/download/v3.1.4/cosign-linux-amd64
printf '%s  %s\n' 424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa "$map/cosign" | sha256sum --check -
chmod 755 "$map/cosign"
"$map/cosign" version
echo "$map" >> "$GITHUB_PATH"
