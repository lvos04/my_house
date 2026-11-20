#!/usr/bin/env bash
set -euo pipefail
prefix=git.medialeon.nl/leon/my_house
versie=$(tr -d '[:space:]' < VERSION)
[[ $TAG == edge || $TAG == "$versie" ]]
if [[ $TAG == edge ]]; then versie="$versie-edge"; fi
proefpid=
proefmap=
bouwerconfig=
bouwer=
# shellcheck disable=SC2317,SC2329 # Wordt door de EXIT-trap aangeroepen.
opruimen() {
  if [[ -n $proefpid ]]; then kill "$proefpid" 2>/dev/null || true; wait "$proefpid" 2>/dev/null || true; fi
  if [[ -n $proefmap ]]; then rm -rf -- "$proefmap"; fi
  if [[ -n $bouwer ]]; then docker buildx rm "$bouwer" >/dev/null; fi
  if [[ -n $bouwerconfig ]]; then rm -rf -- "$bouwerconfig"; fi
}
trap opruimen EXIT
if [[ $PUBLICEREN == true ]]; then
  : "${COSIGN_PRIVATE_KEY:?Ondertekensleutel ontbreekt}" "${COSIGN_PASSWORD:?Cosign-wachtwoord ontbreekt}"
  : "${COSIGN_PUBLIC_KEY:?Publieke verificatiesleutel ontbreekt}"
  : "${REGISTRY_USER:?Registergebruiker ontbreekt}" "${REGISTRY_TOKEN:?Pakketschrijfrecht ontbreekt}"
  # shellcheck source=.forgejo/ci/registry.sh
  source .forgejo/ci/registry.sh
  printf '%s\n' "$COSIGN_PUBLIC_KEY" > "$RUNNER_TEMP/cosign.pub"
  cosign public-key --key env://COSIGN_PRIVATE_KEY | diff -B "$RUNNER_TEMP/cosign.pub" -
else
  # Per job een eigen register op loopback. De antwoordheader identificeert ons proces op de gedeelde runner.
  bash .forgejo/ci/pakketlijst.sh
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker-registry skopeo
  proefmap=$(mktemp -d "$RUNNER_TEMP/register.XXXXXXXX")
  poort=$(python3 -c 'import socket; s=socket.socket(); s.bind(("localhost", 0)); print(s.getsockname()[1]); s.close()')
  kenmerk=$(openssl rand -hex 16)
  # Skopeo mag uitsluitend de zojuist gebouwde lokale OCI-layout als bron gebruiken.
  printf '%s\n' '{"default":[{"type":"reject"}],"transports":{"oci":{"":[{"type":"insecureAcceptAnything"}]}}}' > "$proefmap/beleid.json"
  cat > "$proefmap/config.yml" <<EOF
version: 0.1
storage:
  filesystem:
    rootdirectory: $proefmap/data
http:
  addr: localhost:$poort
  headers:
    X-Shc-Proef: ["$kenmerk"]
EOF
  docker-registry serve "$proefmap/config.yml" > "$proefmap/register.log" 2>&1 &
  proefpid=$!
  curl -fsS --retry 10 --retry-connrefused --retry-delay 1 -D "$proefmap/antwoord" "http://localhost:$poort/v2/" >/dev/null
  tr -d '\r' < "$proefmap/antwoord" | grep -Fxiq "X-Shc-Proef: $kenmerk"
  COSIGN_PASSWORD=$(openssl rand -hex 24)
  export COSIGN_PASSWORD
  cosign generate-key-pair --output-key-prefix "$proefmap/cosign"
fi
# Registreer uitsluitend een ontbrekende arm64-emulator in de geïsoleerde bouwengine.
docker buildx inspect --bootstrap | sed -n 's/^Platforms:[[:space:]]*//p' > "$RUNNER_TEMP/bouwplatformen"
if ! grep -q 'linux/arm64' "$RUNNER_TEMP/bouwplatformen"; then
  docker run --rm --privileged docker.io/tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0 --install arm64
fi
eindpunt=()
if [[ -n ${DOCKER_CERT_PATH:-}${DOCKER_TLS_VERIFY:-} && $(docker context show) == default ]]; then
  # De containerdriver heeft voor een TLS-bouwengine een benoemde context nodig.
  # Alleen het geheugen van de job bevat de tijdelijke context en de bestaande clientcertificaten.
  bouwerconfig=$(mktemp -d /dev/shm/shc-buildx.XXXXXXXX)
  if [[ $PUBLICEREN == true ]]; then cp "$DOCKER_CONFIG/config.json" "$bouwerconfig/"; fi
  export DOCKER_CONFIG="$bouwerconfig"
  docker context create shc-ci >/dev/null
  eindpunt=(shc-ci)
fi
bouwer=$(docker buildx create --driver docker-container --driver-opt cpu-quota=200000 "${eindpunt[@]}")
docker buildx inspect "$bouwer" --bootstrap | sed -n 's/^Platforms:[[:space:]]*//p'
mkdir -p release
for soort in api web; do
  context=.
  if [[ $soort == web ]]; then context=apps/web; fi
  naam="$prefix-$soort"
  if [[ $PUBLICEREN == true ]]; then
    uitvoer="type=image,name=$naam,push-by-digest=true,name-canonical=true,push=true"
  else
    layout="$proefmap/$soort"
    uitvoer="type=oci,tar=false,dest=$layout"
  fi
  docker buildx build --builder "$bouwer" --platform linux/amd64,linux/arm64 --provenance=true --sbom=true \
    --output "$uitvoer" --metadata-file "$RUNNER_TEMP/$soort.json" --build-arg "APP_VERSION=$versie" \
    --label "org.opencontainers.image.source=https://git.medialeon.nl/$GITHUB_REPOSITORY" \
    --label "org.opencontainers.image.revision=$GITHUB_SHA" -f "apps/$soort/Dockerfile" "$context"
  digest=$(jq -er '."containerimage.digest" | select(test("^sha256:[a-f0-9]{64}$"))' "$RUNNER_TEMP/$soort.json")
  echo "$naam@$digest" > "$RUNNER_TEMP/$soort.ref"
  if [[ $PUBLICEREN == true ]]; then
    cosign sign --yes --key env://COSIGN_PRIVATE_KEY "$naam@$digest"
    cosign verify --key "$RUNNER_TEMP/cosign.pub" "$naam@$digest" >/dev/null
  else
    # Elke platformdigest moet zijn eigen attestatiemanifest hebben.
    jq -e '
      [.manifests[] | select(.platform.os == "linux")] as $beelden |
      ([$beelden[].platform.architecture] | sort == ["amd64","arm64"]) and
      ([$beelden[].digest] | sort) == ([.manifests[] | select(.annotations["vnd.docker.reference.type"] == "attestation-manifest") |
        .annotations["vnd.docker.reference.digest"]] | sort)' "$layout/blobs/sha256/${digest#sha256:}"
    lokaal="localhost:$poort/proef/my_house-$soort"
    skopeo --tmpdir "$proefmap" --policy "$proefmap/beleid.json" copy --all --preserve-digests --dest-tls-verify=false \
      "oci:$layout" "docker://$lokaal:$TAG"
    cosign sign --yes --allow-http-registry --use-signing-config=false --tlog-upload=false --key "$proefmap/cosign.key" "$lokaal@$digest"
    cosign verify --allow-http-registry --insecure-ignore-tlog --key "$proefmap/cosign.pub" "$lokaal@$digest" >/dev/null
    rm -rf -- "$layout"
  fi
done
api=$(cat "$RUNNER_TEMP/api.ref")
web=$(cat "$RUNNER_TEMP/web.ref")
if [[ $PUBLICEREN == true ]]; then
  for ref in "$api" "$web"; do docker buildx imagetools create -t "${ref%@*}:$TAG" "$ref"; done
fi
jq -n --arg api "$api" --arg web "$web" '{api:$api,web:$web}' > release/digests.json
