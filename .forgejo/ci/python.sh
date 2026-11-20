#!/usr/bin/env bash
# Een venv per job. De runtimepakketten komen uit hetzelfde hashlock als de API-image.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
bash .forgejo/ci/pakketlijst.sh
pakketten=(python3.12-venv)
if [[ $1 != e2e ]]; then pakketten+=(pipx); fi
apt-get -o Acquire::Retries=2 install -y --no-install-recommends "${pakketten[@]}"
venv="$RUNNER_TEMP/shc-venv"
python3.12 -m venv "$venv"
echo "$venv/bin" >> "$GITHUB_PATH"
if [[ ${1:-} == audit ]]; then exit 0; fi
grep -vE '^[./]' apps/api/requirements.txt > "$RUNNER_TEMP/lock.txt"
"$venv/bin/pip" install --require-hashes --only-binary=:all: -r "$RUNNER_TEMP/lock.txt"
if [[ $1 == backend ]]; then
  "$venv/bin/pip" freeze > "$RUNNER_TEMP/locked.txt"
  "$venv/bin/pip" install -c "$RUNNER_TEMP/locked.txt" -e 'integrations[test]' -e 'apps/api[dev]'
else
  "$venv/bin/pip" install --no-deps -e integrations -e apps/api
fi
"$venv/bin/pip" check
