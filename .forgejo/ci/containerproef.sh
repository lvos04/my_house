#!/usr/bin/env bash
# In de bouwengine delen proefclient en testcontainers loopback en dezelfde tijdelijke bindmountpaden.
set -euo pipefail
soort=$1
imagebestand="$RUNNER_TEMP/proefomgeving.id"
proefbestand="$RUNNER_TEMP/proefomgeving.volume"
if [[ $soort == ruim ]]; then
  # Afsluitende stap: ook na afbreken de proefclient, alles wat de proef startte en de Compose-stacks weg.
  if [[ -s $proefbestand ]]; then
    docker ps -aq --filter "label=nl.medialeon.ci.proef=$(cat "$proefbestand")" | xargs -r docker rm -fv >/dev/null
    for project in "my-house-$SHC_VERSION" "my-house-e2e-$SHC_VERSION"; do
      docker compose -p "$project" down -v --remove-orphans >/dev/null 2>&1 || true
    done
    docker volume rm "$(cat "$proefbestand")" >/dev/null 2>&1 || true
  fi
  exit 0
fi
docker buildx build --load --iidfile "$imagebestand" .forgejo/ci/proefomgeving
volume=$(docker volume create)
echo "$volume" > "$proefbestand"
container=
# shellcheck disable=SC2317,SC2329 # Wordt door de EXIT-trap aangeroepen.
opruimen() {
  if [[ -n $container ]]; then docker rm -fv "$container" >/dev/null; fi
  docker volume rm "$volume" >/dev/null
}
trap opruimen EXIT
werk=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
bron=/opt/shc-proef
# Het gedeelde slotvolume hangt naast de proefwerkmap in de container; geen pad van de runnerhost.
slotmap=$bron-sloten
container=$(docker create --network host --cpus 2 --shm-size=1g \
  --label "nl.medialeon.ci.proef=$volume" -e "CI_PROEF=$volume" \
  --mount "type=volume,source=$volume,target=$werk" \
  --mount "type=volume,source=my-house-ci-sloten,target=$slotmap" \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --workdir "$bron" -e TMPDIR=/opt/shc-tijdelijk -e SHC_PROEFVOLUME="$werk" -e SHC_VERSION -e CI=true "$(cat "$imagebestand")" \
  flock "$slotmap/stack.lock" nice -n 10 bash .forgejo/ci/stackproef.sh "$soort")
git -c tar.umask=0022 archive HEAD | docker cp - "$container:$bron"
status=0
docker start -a "$container" || status=$?
if (( status == 0 )); then status=$(docker wait "$container"); fi
if [[ $soort == allinone ]]; then
  # Alleen een in deze proef geschreven logboek mag als bewijs worden opgeslagen.
  if docker cp "$container:$bron/herstelproef.md" "$RUNNER_TEMP/herstelproef.md" 2>/dev/null; then
    echo "herstelbewijs=true" >> "$GITHUB_OUTPUT"
  elif (( status == 0 )); then
    echo "Het gemeten herstelbewijs ontbreekt." >&2
    exit 1
  fi
elif (( status != 0 )); then
  mkdir -p infra/e2e/.output
  docker cp "$container:$bron/infra/e2e/.output/." infra/e2e/.output/ || true
fi
exit "$status"
