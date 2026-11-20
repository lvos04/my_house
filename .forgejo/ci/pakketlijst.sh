#!/usr/bin/env bash
# Een tijdelijke fout van een Ubuntu-spiegel mag opnieuw worden geprobeerd. APT controleert de handtekening zelf.
set -euo pipefail
for poging in 1 2 3; do
  if apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 \
      -o APT::Update::Error-Mode=any update -qq; then
    exit 0
  fi
  if (( poging < 3 )); then sleep "$((poging * 5))"; fi
done
exit 1
