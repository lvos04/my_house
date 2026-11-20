#!/usr/bin/env bash
# De tokenheader gaat via stdin naar curl en de responstekst blijft buiten het joblog.
set -euo pipefail
api() { printf 'Authorization: token %s\n' "${JOB_TOKEN:?}" | curl -sS --header @- "$@"; }
releases="https://git.medialeon.nl/api/v1/repos/leon/my_house/releases"
info="$RUNNER_TEMP/release.json"
V=$(tr -d '[:space:]' < VERSION)
[[ $V =~ ^[0-9]+\.[0-9]{1,2}\.[0-9]{1,2}$ ]]
status=$(api -o "$info" -w '%{http_code}' "$releases/tags/v$V")
case $status in
  200) bestaand=$(jq -er 'if .draft then "false" else "true" end' "$info");;
  404) bestaand=false;;
  *) echo "Release opzoeken gaf HTTP $status" >&2; exit 1;;
esac
if [[ $1 == opzoeken ]]; then
  printf 'version=%s\nexists=%s\n' "$V" "$bestaand" >> "$GITHUB_OUTPUT"
  exit 0
fi
[[ $1 == publiceren && $PUBLICEREN == true && $bestaand == false ]]
if [[ $status == 404 ]]; then
  jq -n --arg tag "v$V" --arg sha "$GITHUB_SHA" --rawfile body release/notities.md \
    '{tag_name:$tag, target_commitish:$sha, name:("Smart Home Control " + $tag), body:$body, draft:true}' > "$RUNNER_TEMP/concept.json"
  api --fail -H 'Content-Type: application/json' --data-binary "@$RUNNER_TEMP/concept.json" "$releases" > "$info"
fi
id=$(jq -er --arg sha "$GITHUB_SHA" 'select(.draft and .target_commitish == $sha) | .id' "$info")
assets="$releases/$id/assets"
api --fail "$assets" > "$RUNNER_TEMP/bijlagen.json"
for file in release/bijlagen/*; do
  naam=${file##*/}
  oud=$(jq -r --arg n "$naam" '.[] | select(.name == $n) | .id' "$RUNNER_TEMP/bijlagen.json")
  # Bij een herstart mag alleen een bijlage van het gecontroleerde concept worden vernieuwd.
  if [[ -n $oud ]]; then api --fail -X DELETE "$assets/$oud" >/dev/null; fi
  api --fail -F "attachment=@$file" "$assets?name=$naam" | jq -e --arg n "$naam" '.name == $n' >/dev/null
done
api --fail -X PATCH -H 'Content-Type: application/json' --data '{"draft":false}' "$releases/$id" \
  | jq -er 'select(.draft == false) | .html_url'
