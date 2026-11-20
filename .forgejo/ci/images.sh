#!/usr/bin/env bash
set -euo pipefail
prefix=git.medialeon.nl/leon/my_house
versie=$(tr -d '[:space:]' < VERSION)
[[ $TAG == edge || $TAG == "$versie" ]]
if [[ $TAG == edge ]]; then versie="$versie-edge"; fi
if [[ $PUBLICEREN == true ]]; then
  : "${COSIGN_PRIVATE_KEY:?Ondertekensleutel ontbreekt}" "${COSIGN_PASSWORD:?Cosign-wachtwoord ontbreekt}"
  : "${COSIGN_PUBLIC_KEY:?Publieke verificatiesleutel ontbreekt}"
  : "${REGISTRY_USER:?Registergebruiker ontbreekt}" "${REGISTRY_TOKEN:?Pakketschrijfrecht ontbreekt}"
  # shellcheck source=.forgejo/ci/registry.sh
  source .forgejo/ci/registry.sh
  printf '%s\n' "$COSIGN_PUBLIC_KEY" > "$RUNNER_TEMP/cosign.pub"
  cosign public-key --key env://COSIGN_PRIVATE_KEY | diff -B "$RUNNER_TEMP/cosign.pub" -
fi
bouwer=$(docker buildx create --driver docker-container)
trap 'docker buildx rm "$bouwer" >/dev/null' EXIT
docker buildx inspect "$bouwer" --bootstrap | tee "$RUNNER_TEMP/platformen"
grep -q 'linux/arm64' "$RUNNER_TEMP/platformen"
mkdir -p release
for soort in api web; do
  context=.
  if [[ $soort == web ]]; then context=apps/web; fi
  naam="$prefix-$soort"
  if [[ $PUBLICEREN == true ]]; then
    uitvoer="type=image,name=$naam,push-by-digest=true,name-canonical=true,push=true"
  else
    uitvoer="type=oci,dest=$RUNNER_TEMP/$soort.oci"
  fi
  docker buildx build --builder "$bouwer" --platform linux/amd64,linux/arm64 --provenance=true --sbom=true \
    --output "$uitvoer" --metadata-file "$RUNNER_TEMP/$soort.json" --build-arg "APP_VERSION=$versie" \
    --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
    --label "org.opencontainers.image.revision=$GITHUB_SHA" -f "apps/$soort/Dockerfile" "$context"
  digest=$(jq -er '."containerimage.digest" | select(test("^sha256:[a-f0-9]{64}$"))' "$RUNNER_TEMP/$soort.json")
  echo "$naam@$digest" > "$RUNNER_TEMP/$soort.ref"
  if [[ $PUBLICEREN == true ]]; then
    cosign sign --yes --key env://COSIGN_PRIVATE_KEY "$naam@$digest"
    cosign verify --key "$RUNNER_TEMP/cosign.pub" "$naam@$digest" >/dev/null
  else
    # De proef valideert dezelfde tweevoudige index en attestaties zonder naar een register te schrijven.
    tar -xOf "$RUNNER_TEMP/$soort.oci" "blobs/sha256/${digest#sha256:}" | jq -e '
      ([.manifests[] | select(.platform.os == "linux") | .platform.architecture] | sort == ["amd64","arm64"]) and
      ([.manifests[] | select(.annotations["vnd.docker.reference.type"] == "attestation-manifest")] | length == 2)'
    rm "$RUNNER_TEMP/$soort.oci"
  fi
done
api=$(cat "$RUNNER_TEMP/api.ref")
web=$(cat "$RUNNER_TEMP/web.ref")
if [[ $PUBLICEREN == true ]]; then
  for ref in "$api" "$web"; do docker buildx imagetools create -t "${ref%@*}:$TAG" "$ref"; done
fi
jq -n --arg api "$api" --arg web "$web" '{api:$api,web:$web}' > release/digests.json
