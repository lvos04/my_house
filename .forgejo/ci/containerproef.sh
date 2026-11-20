#!/usr/bin/env bash
# In de bouwengine delen proefclient en testcontainers loopback en dezelfde tijdelijke bindmountpaden.
set -euo pipefail
soort=$1
imagebestand="$RUNNER_TEMP/proefomgeving.id"
docker buildx build --load --iidfile "$imagebestand" .forgejo/ci/proefomgeving
volume=$(docker volume create)
container=
# shellcheck disable=SC2329 # Wordt door de EXIT-trap aangeroepen.
opruimen() {
  if [[ -n $container ]]; then docker rm -fv "$container" >/dev/null; fi
  docker volume rm "$volume" >/dev/null
}
trap opruimen EXIT
werk=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
slotmap=/home/transcode/truenas-v2/.lokaal
container=$(docker create --network host --cpus 2 \
  --mount "type=volume,source=$volume,target=$werk" \
  --mount "type=volume,source=my-house-ci-sloten,target=$slotmap" \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --workdir "$werk" -e TMPDIR="$werk" -e SHC_VERSION -e CI=true "$(cat "$imagebestand")" \
  flock "$slotmap/stack.lock" nice -n 10 bash .forgejo/ci/stackproef.sh "$soort")
git archive HEAD | docker cp - "$container:$werk"
status=0
docker start -a "$container" || status=$?
if (( status == 0 )); then status=$(docker wait "$container"); fi
if [[ $soort == allinone ]]; then
  # Alleen een in deze proef geschreven logboek mag als bewijs worden opgeslagen.
  if docker cp "$container:$werk/herstelproef.md" "$RUNNER_TEMP/herstelproef.md" 2>/dev/null; then
    echo "herstelbewijs=true" >> "$GITHUB_OUTPUT"
  elif (( status == 0 )); then
    echo "Het gemeten herstelbewijs ontbreekt." >&2
    exit 1
  fi
elif (( status != 0 )); then
  mkdir -p infra/e2e/.output
  docker cp "$container:$werk/infra/e2e/.output/." infra/e2e/.output/ || true
fi
exit "$status"
