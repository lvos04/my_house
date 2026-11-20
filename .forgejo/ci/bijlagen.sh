#!/usr/bin/env bash
set -euo pipefail
versie=$(tr -d '[:space:]' < VERSION)
cd release/bijlagen
jq -e 'def beeld($naam): test("^git[.]medialeon[.]nl/leon/my_house-" + $naam + "@sha256:[0-9a-f]{64}$");
  (.api | beeld("api")) and (.web | beeld("web"))' digests.json >/dev/null
if [[ $SIGNED == true ]]; then
  sha256sum --check "huis-v$versie.apk.sha256"
  grep -Eq '^[0-9a-f]{64}$' "huis-v$versie.apk.certificate-sha256"
fi
tijdelijk=$(mktemp -d "$RUNNER_TEMP/cosign.XXXXXXXX")
trap 'rm -rf -- "$tijdelijk"' EXIT
umask 077
sign_opties=()
verify_opties=()
if [[ $PUBLICEREN == true ]]; then
  : "${COSIGN_PRIVATE_KEY:?}" "${COSIGN_PASSWORD:?}" "${COSIGN_PUBLIC_KEY:?}"
  sleutel=env://COSIGN_PRIVATE_KEY
  printf '%s\n' "$COSIGN_PUBLIC_KEY" > "$tijdelijk/cosign.pub"
else
  # Uitsluitend de lokale proef gebruikt een wegwerpsleutel zonder publiek transparantielog.
  COSIGN_PASSWORD=$(openssl rand -hex 24)
  export COSIGN_PASSWORD
  cosign generate-key-pair --output-key-prefix "$tijdelijk/cosign"
  sleutel="$tijdelijk/cosign.key"
  sign_opties=(--use-signing-config=false --tlog-upload=false)
  verify_opties=(--insecure-ignore-tlog)
fi
for file in *; do
  cosign sign-blob --yes --key "$sleutel" "${sign_opties[@]}" --bundle "$file.sigstore.json" "$file"
  cosign verify-blob --key "$tijdelijk/cosign.pub" "${verify_opties[@]}" --bundle "$file.sigstore.json" "$file"
done
cp ../../docs/release-notes.md ../notities.md
{
  printf '\n## Ondertekende images\n\n'
  jq -r '.[] | "- `" + . + "`"' digests.json
  printf '\nControleer met de vertrouwde publieke releasesleutel.\n\n'
  printf '\x60\x60\x60sh\ncosign verify --key cosign.pub %s\n' "$(jq -r .api digests.json)"
  printf 'cosign verify-blob --key cosign.pub --bundle digests.json.sigstore.json digests.json\n\x60\x60\x60\n'
  if [[ $SIGNED == false ]]; then
    printf '\nDeze release bevat alleen de server-images. De Android-releasesleutel is nog niet ingesteld.\n'
  fi
} >> ../notities.md
