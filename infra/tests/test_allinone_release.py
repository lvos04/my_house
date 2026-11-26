"""De tagroute, de weigering van een bezette versie en het bewijs voor het formulier."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.load((ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)  # noqa: S506
MANIFEST = yaml.safe_load((ROOT / "deploy/manifest/my_house.manifest.yaml").read_text())
VERSIE = (ROOT / "VERSION").read_text().strip()
REGISTERPROEF = """#!/bin/sh
for doel do :; done
case "$doel" in
  "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/releases/tags/$GITHUB_REF_NAME") echo "$RELEASE_STATUS" ;;
  https://ghcr.io/token) echo '{"token":"proef"}' ;;
  "https://ghcr.io/v2/${IMAGE#ghcr.io/}/manifests/${GITHUB_REF_NAME#v}") echo "$IMAGE_STATUS" ;;
  *) exit 2 ;;
esac
"""


class AllesInEenRelease(unittest.TestCase):
    def test_alleen_de_gecontroleerde_tag_publiceert(self):
        self.assertEqual(WORKFLOW["on"], {"push": {"tags": ["v*.*.*"]}})
        self.assertEqual(WORKFLOW["env"]["IMAGE"], MANIFEST["image"]["repository"])
        self.assertEqual(MANIFEST["version"], VERSIE)
        stappen = WORKFLOW["jobs"]["image"]["steps"]
        controle = next(s for s in stappen if s.get("run") == "bash infra/scripts/controleer_release.sh")
        bouw = next(s for s in stappen if s.get("uses", "").startswith("docker/build-push-action@"))
        self.assertLess(stappen.index(controle), stappen.index(bouw))
        instellingen = bouw["with"]
        self.assertEqual(instellingen["file"], "deploy/allinone/Dockerfile")
        self.assertEqual(instellingen["tags"], "${{ env.IMAGE }}:${{ steps." + controle["id"] + ".outputs.version }}")
        for veld in ("push", "provenance", "sbom"):
            self.assertEqual(instellingen[veld], "true")
        self.assertEqual(WORKFLOW["jobs"]["release"]["needs"], "image")

    def test_controle_stopt_bij_verkeerde_tag_bezette_versie_en_onzekerheid(self):
        with tempfile.TemporaryDirectory() as tijdelijk:
            werk = Path(tijdelijk)
            curl = werk / "curl"
            curl.write_text(REGISTERPROEF)
            curl.chmod(0o700)
            uitvoer = werk / "uitvoer"
            env = dict(os.environ, GH_TOKEN="", GITHUB_ACTOR="proef", GITHUB_API_URL="https://voorbeeld.invalid",
                       GITHUB_OUTPUT=str(uitvoer), GITHUB_REPOSITORY="lvos04/my_house", IMAGE=WORKFLOW["env"]["IMAGE"],
                       PATH=f"{werk}:{os.environ['PATH']}")
            for ref, tag, release, image, verwacht in (
                ("tag", f"v{VERSIE}", 404, 404, True),
                ("branch", f"v{VERSIE}", 404, 404, False),
                ("tag", "v0.0.0", 404, 404, False),
                ("tag", f"v{VERSIE}", 200, 404, False),
                ("tag", f"v{VERSIE}", 404, 200, False),
                ("tag", f"v{VERSIE}", 403, 404, False),
                ("tag", f"v{VERSIE}", 404, 503, False),
            ):
                with self.subTest(ref=ref, tag=tag, release=release, image=image):
                    uitvoer.write_text("")
                    proces = subprocess.run(  # noqa: S603 - echte beslislogica met een lokale registersimulator
                        ["/bin/bash", "infra/scripts/controleer_release.sh"], cwd=ROOT, capture_output=True,
                        env=dict(env, GITHUB_REF_TYPE=ref, GITHUB_REF_NAME=tag,
                                 RELEASE_STATUS=str(release), IMAGE_STATUS=str(image)),
                    )
                    self.assertEqual(proces.returncode == 0, verwacht, proces.stderr)
                    self.assertEqual(uitvoer.read_text(), f"version={VERSIE}\n" if verwacht else "")

    def test_bijlage_neemt_de_bouwdigest_en_manifestidentiteit_over(self):
        stappen = WORKFLOW["jobs"]["release"]["steps"]
        opdracht = next(s["run"] for s in stappen if "jq -n" in s.get("run", ""))
        digest = "sha256:" + "a" * 64
        image = MANIFEST["image"]["repository"]
        with tempfile.TemporaryDirectory() as werk:
            env = dict(os.environ, DIGEST=digest, IMAGE=image, VERSION=VERSIE)
            subprocess.run(["/bin/bash", "-ec", opdracht], cwd=werk, env=env, check=True)  # noqa: S603
            bewijs = json.loads((Path(werk) / "release/digests.json").read_text())
            self.assertEqual(bewijs, {"app_id": MANIFEST["app_id"], "tag": VERSIE, "version": VERSIE,
                                      "image": image, "digest": digest})
