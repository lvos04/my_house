"""Toestandstabel voor de strikte manifestvalidator.

Manifest                         Exitcode   Verwachting
Ongewijzigd                       0          Geldig
Met parameter HUB_URL             1          Schema weigert hoofdletters
Met parameter hub_url             1          reserved_env
Met omgevingsvariabele HUB_URL    1          reserved_env
Met compose-veld                  1          compose_forbidden
Met apparaat zonder reden         1          device_without_reason
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parents[2] / "deploy/manifest/my_house.manifest.yaml"


def valideer(pad):
    opdracht = [os.environ.get("JL_MANIFEST", "jl-manifest"), "validate", "--strict", "--format", "json", str(pad)]
    proces = subprocess.run(opdracht, capture_output=True, text=True, check=False)  # noqa: S603 - lokale validator
    if not proces.stdout:
        raise AssertionError(proces.stderr)
    return proces.returncode, json.loads(proces.stdout)


class Manifestproef(unittest.TestCase):
    def test_manifest_is_strikt_geldig(self):
        code, rapport = valideer(MANIFEST)
        self.assertEqual(code, 0, rapport)
        self.assertEqual(rapport["result"], "geldig")

    def controleer_weigering(self, manifest, *verwacht, veld=None):
        with tempfile.TemporaryDirectory() as mapnaam:
            kopie = Path(mapnaam) / MANIFEST.name
            kopie.write_text(yaml.safe_dump(manifest, sort_keys=False))
            code, rapport = valideer(kopie)
        self.assertEqual(code, 1, rapport)
        fouten = {f["code"] for f in rapport["findings"] if f["severity"] == "error"
                  and (veld is None or f["path"] == veld)}
        self.assertTrue(fouten.intersection(verwacht), rapport)

    def controleer_parameter(self, naam, *verwacht):
        manifest = yaml.safe_load(MANIFEST.read_text())
        veld = f"parameters[{len(manifest['parameters'])}].name"
        manifest["parameters"].append(
            {"name": naam, "type": "url", "source": "wizard", "required": False, "description_nl": "Proefadres."}
        )
        self.controleer_weigering(manifest, *verwacht, veld=veld)

    def test_parameter_met_hoofdletters(self):
        # De huidige schemacontrole weigert hoofdletters al vóór de reserveringsregel.
        self.controleer_parameter("HUB_URL", "reserved_env", "invalid_format")

    def test_gereserveerde_parameter(self):
        self.controleer_parameter("hub_url", "reserved_env")

    def test_gereserveerde_omgevingsvariabele(self):
        manifest = yaml.safe_load(MANIFEST.read_text())
        manifest["custom_app"]["env"].append({"name": "HUB_URL", "value": "https://voorbeeld.invalid"})
        self.controleer_weigering(manifest, "reserved_env")

    def test_compose_veld(self):
        manifest = yaml.safe_load(MANIFEST.read_text())
        manifest["compose"] = {}
        self.controleer_weigering(manifest, "compose_forbidden")

    def test_apparaat_zonder_reden(self):
        manifest = yaml.safe_load(MANIFEST.read_text())
        manifest["privileges"]["devices"] = [
            {"host_device": "/dev/serial/by-id/synthetische-proef", "container_device": "/dev/zigbee"}
        ]
        manifest.pop("privilege_reason", None)
        self.controleer_weigering(manifest, "device_without_reason")
