#!/usr/bin/env bash
# Wordt uitgevoerd vanuit apps/android. Een proef gebruikt een kortlevende eigen sleutel.
set -euo pipefail
umask 077
sleutelmap=$(mktemp -d "$RUNNER_TEMP/apk.XXXXXXXX")
trap 'rm -rf -- "$sleutelmap"' EXIT
export SHC_SIGNING_STORE_FILE="$sleutelmap/release.p12"
if [[ $PUBLICEREN == true ]]; then
  if [[ -z $ANDROID_KEYSTORE_B64$SHC_SIGNING_STORE_PASSWORD$SHC_SIGNING_KEY_ALIAS$SHC_SIGNING_KEY_PASSWORD ]]; then
    echo 'signed=false' >> "$GITHUB_OUTPUT"
    echo 'De release bevat alleen de server-images omdat de Android-sleutel ontbreekt.'
    exit 0
  fi
  : "${ANDROID_KEYSTORE_B64:?}" "${SHC_SIGNING_STORE_PASSWORD:?}" "${SHC_SIGNING_KEY_ALIAS:?}" "${SHC_SIGNING_KEY_PASSWORD:?}"
  printf '%s' "$ANDROID_KEYSTORE_B64" | base64 -d > "$SHC_SIGNING_STORE_FILE"
else
  SHC_SIGNING_STORE_PASSWORD=$(openssl rand -hex 24)
  SHC_SIGNING_KEY_PASSWORD=$SHC_SIGNING_STORE_PASSWORD
  SHC_SIGNING_KEY_ALIAS=proef
  export SHC_SIGNING_STORE_PASSWORD SHC_SIGNING_KEY_PASSWORD SHC_SIGNING_KEY_ALIAS
  keytool -genkeypair -keystore "$SHC_SIGNING_STORE_FILE" -alias "$SHC_SIGNING_KEY_ALIAS" -keyalg RSA \
    -storepass:env SHC_SIGNING_STORE_PASSWORD -keypass:env SHC_SIGNING_KEY_PASSWORD -validity 1 -dname "CN=Lokale CI-proef"
fi
unset ANDROID_KEYSTORE_B64
./gradlew --no-daemon --max-workers=2 :app:assembleRelease
bt="${ANDROID_HOME:?}/build-tools/35.0.0"
out=app/build/outputs/apk/release
apk="$out/app-release.apk"
if [[ ! -f $apk ]]; then
  "$bt/zipalign" -f -p 4 "$out/app-release-unsigned.apk" "$sleutelmap/aligned.apk"
  apk="$sleutelmap/signed.apk"
  "$bt/apksigner" sign --ks "$SHC_SIGNING_STORE_FILE" --ks-key-alias "$SHC_SIGNING_KEY_ALIAS" \
    --ks-pass env:SHC_SIGNING_STORE_PASSWORD --key-pass env:SHC_SIGNING_KEY_PASSWORD --out "$apk" "$sleutelmap/aligned.apk"
fi
"$bt/apksigner" verify --verbose --print-certs "$apk" > "$sleutelmap/certs.txt"
got=$(awk '/certificate SHA-256 digest:/ {print $NF; exit}' "$sleutelmap/certs.txt")
want=$(keytool -exportcert -keystore "$SHC_SIGNING_STORE_FILE" -alias "$SHC_SIGNING_KEY_ALIAS" \
  -storepass:env SHC_SIGNING_STORE_PASSWORD | sha256sum | cut -d' ' -f1)
[[ -n $got && $got == "$want" ]]
cp "$apk" "huis-v$V.apk"
sha256sum "huis-v$V.apk" > "huis-v$V.apk.sha256"
echo "$got" > "huis-v$V.apk.certificate-sha256"
echo 'signed=true' >> "$GITHUB_OUTPUT"
