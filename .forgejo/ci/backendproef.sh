#!/usr/bin/env bash
# Een runner kan de gevraagde shm-grootte van de jobcontainer negeren.
set -euo pipefail
if [[ -z ${SHC_CI_PGROOT:-} ]]; then exec bash .forgejo/ci/postgresproef.sh backend; fi
vrij=$(df -Pk "$SHC_CI_PGROOT" | awk 'NR == 2 {print $4}')
if (( vrij >= 1572864 )); then exec bash .forgejo/ci/postgresproef.sh backend; fi

# Hergebruik uitsluitend de basisimage van deze eigen job op de geïsoleerde bouwengine.
naam=$(hostname)
id=
beeld=
if [[ $naam =~ ^([0-9a-f]{12}|[0-9a-f]{64})$ ]] &&
   gegevens=$(docker container inspect --format '{{.Id}} {{.Image}}' "$naam" 2>/dev/null); then
  read -r id beeld <<< "$gegevens"
fi
if [[ $id != "$naam"* || ! $beeld =~ ^sha256:[0-9a-f]{64}$ ]]; then
  exec bash .forgejo/ci/postgresproef.sh backend
fi

werk=$(mktemp -d "$RUNNER_TEMP/backend.XXXXXXXX")
kenmerk=$(cat /proc/sys/kernel/random/uuid)
opruimen() {
  rm -rf -- "$werk"
  local eigen
  eigen=$(docker container ls -aq --no-trunc --filter "label=nl.medialeon.backendproef=$kenmerk") || return 1
  if [[ -n $eigen ]] && ! docker rm -fv "$eigen" >/dev/null; then
    # De daemon kan de testcontainer intussen zelf hebben verwijderd.
    eigen=$(docker container ls -aq --no-trunc --filter "label=nl.medialeon.backendproef=$kenmerk") || return 1
    [[ -z $eigen ]]
  fi
}
trap opruimen EXIT
trap 'exit 130' INT TERM HUP

# Een bundel draagt de historische revisies over zonder Git-instellingen of inloggegevens.
git -c pack.threads=2 bundle create "$werk/bron.bundle" --all HEAD
echo 'De API-suite gebruikt een eigen proefcontainer met 2 GiB gedeeld geheugen.'
docker run --rm -i --pull never --label "nl.medialeon.backendproef=$kenmerk" \
  --cpus 2 --memory 3g --shm-size 2g --workdir /opt/shc-bron --entrypoint /bin/bash \
  -e CI=true -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e SHC_CI_PGROOT=/dev/shm \
  -e RUNNER_TEMP=/opt/shc-tijdelijk -e GITHUB_PATH=/opt/shc-tijdelijk/pad -e "BRONCOMMIT=$(git rev-parse HEAD)" \
  "$beeld" -euo pipefail -c '
    mkdir -p "$RUNNER_TEMP"
    cat > "$RUNNER_TEMP/bron.bundle"
    git init --quiet
    git fetch --quiet --tags "$RUNNER_TEMP/bron.bundle" HEAD
    git checkout --quiet --detach FETCH_HEAD
    [[ $(git rev-parse HEAD) == "$BRONCOMMIT" ]]
    rm "$RUNNER_TEMP/bron.bundle"
    bash .forgejo/ci/python.sh backend
    export PATH="$RUNNER_TEMP/shc-venv/bin:$PATH"
    exec bash .forgejo/ci/postgresproef.sh backend
  ' < "$werk/bron.bundle" &
wait "$!"
