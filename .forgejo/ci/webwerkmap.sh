#!/usr/bin/env bash
# Native npm-pakketten moeten ook op runners met een noexec-checkout kunnen starten.
set -euo pipefail
werk=$(mktemp -d "${RUNNER_TEMP:?}/webbron.XXXXXXXX")
install -m 755 "$(type -P true)" "$werk/uitvoerproef"
if ! "$werk/uitvoerproef"; then
  rm "$werk/uitvoerproef"
  rmdir "$werk"
  werk=$(mktemp -d /opt/webbron.XXXXXXXX)
fi
git archive HEAD | tar -x -C "$werk"
echo "WEBBRON=$werk" >> "$GITHUB_ENV"
echo "pad=$werk" >> "$GITHUB_OUTPUT"
