"""De installatieroute en de herkomst van de runtimepakketten."""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy/manifest/my_house.manifest.yaml"
FORMULIER = ROOT / "deploy/truenas/CUSTOM_APP.md"


class Installatie(unittest.TestCase):
    def test_terugvalroutes_verwijzen_naar_de_offline_voorbereiding(self):
        anker = "#offline-terugvalkopie-voorbereiden"
        routes = {
            "docs/installatie-truenas.md": ("Updaten en terugdraaien", "Overstap van een bestaande installatie"),
            "docs/onderhoud.md": ("Updates en terugdraaien", "In de app (aanbevolen)",
                                  "Overstappen van een bestaande Compose-installatie", "Database-dump terugzetten"),
            "docs/release-notes.md": ("Installeren en bijwerken",),
            "docs/api.md": ("Systeem",),
            "docs/platform-v2/00_START/LEES_MIJ_EERST.md": ("Belangrijke begrenzingen",),
            "docs/platform-v2/03_INSTALLATIE/I05_MY_HOUSE.md": ("4. Terugdraaien / verwijderen",),
            "docs/platform-v2/04_BESTAANDE_APPS/A20_MY_HOUSE.md": ("Testcase en beslissing",),
            "docs/adr/ADR-0002-hub-koppeling-via-bestaande-routes.md": ("Toestandstabel bij versiewissels",),
            "docs/adr/ADR-0004-alles-in-een-custom-app.md": ("Besluit (ontwerp)",),
            "docs/adr/ADR-0015-alles-in-een-gebouwd.md": ("Start en opslag",),
            "docs/adr/ADR-0016-ondertekende-hub-verzoeken.md": ("Besluiten",),
            "docs/ontwerp/PEIL_MIGRATIE.md": ("Systeem › Herstellen en verhuizen",),
        }
        for naam, koppen in routes.items():
            tekst = (ROOT / naam).read_text()
            for kop in koppen:
                with self.subTest(pad=naam, kop=kop):
                    sectie = tekst.split(" " + kop + "\n", 1)[1].split("\n#", 1)[0]
                    pad = Path(naam)
                    doel = Path("docs/onderhoud.md")
                    verwijzing = "" if pad == doel else os.path.relpath(doel, pad.parent)
                    self.assertIn(f"({verwijzing}{anker})", sectie)
        onderhoud = (ROOT / "docs/onderhoud.md").read_text()
        procedure = onderhoud.split("### Offline terugvalkopie voorbereiden\n", 1)[1].split("\n#", 1)[0]
        for vereist in ("shc-api prepare-connector-fallback", "exitcode 0", "blijft de oude worker uit"):
            self.assertIn(vereist, procedure)
        installatie = (ROOT / "docs/installatie-truenas.md").read_text().split("## Updaten en terugdraaien\n")[1]
        self.assertLess(installatie.index("shc-api prepare-connector-fallback"), installatie.index("Kies pas na"))
        self.assertIn("exitcode 0 de vaste tag", installatie.split("Kies pas na", 1)[1])
        for vereist in ("terugval-app", "/opt/venv/bin/shc-api", "SHC_TERUGVAL_ID"):
            self.assertIn(vereist, procedure)

    def test_bestaande_compose_bindt_het_startbestand_bij_api_en_worker(self):
        start = ROOT / "apps/api/src/shc_api/terugval_start.py"
        receipt = str(uuid4())
        command = ["docker", "compose", "-f", "infra/compose/compose.yaml", "-f", "infra/compose/terugval.yaml",
                   "config", "--format", "json"]
        env = dict(os.environ, SHC_ROOT="/proefkopie", SHC_TERUGVAL_START=str(start), SHC_TERUGVAL_ID=receipt)
        config = subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True)  # noqa: S603 - vaste config
        services = json.loads(config.stdout)["services"]
        for naam, opdracht in (("api", "serve"), ("worker", "worker")):
            service = services[naam]
            self.assertEqual(service["entrypoint"],
                             ["/opt/venv/bin/python", "-IB", "/opt/venv/bin/shc-api", "terugval"])
            self.assertEqual(service["command"], [opdracht])
            self.assertEqual(service["environment"]["SHC_TERUGVAL_ID"], receipt)
            self.assertIn("SHC_DATABASE_URL", service["environment"])
            mount, = [v for v in service["volumes"] if v["target"] == "/opt/venv/bin/shc-api"]
            self.assertEqual(mount["source"], str(start))
            self.assertTrue(mount["read_only"])
            self.assertFalse(mount["bind"].get("create_host_path", False))
            marker, = [v for v in service["volumes"] if v["target"] == "/run/shc-terugval-pgversie"]
            self.assertEqual(marker["source"], "/proefkopie/postgres/PG_VERSION")
            self.assertTrue(marker["read_only"])
        del env["SHC_TERUGVAL_ID"]
        ontbreekt = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)  # noqa: S603 - vaste config
        self.assertNotEqual(ontbreekt.returncode, 0)
        self.assertIn(b"Terugval-ID", ontbreekt.stderr)

    def test_formulier_is_de_installatieroute(self):
        for name in ("README.md", "docs/installatie-truenas.md", "docs/problemen-oplossen.md", "docs/onderhoud.md",
                     "docs/platform-v2/03_INSTALLATIE/I05_MY_HOUSE.md"):
            sections = (ROOT / name).read_text().lower().split("\n## ")
            text = "\n".join(s for s in sections if not s.startswith("alleen voor ontwikkeling\n"))
            self.assertIn("standaard custom app", text)
            self.assertNotIn("install via yaml", text)
            self.assertNotIn("render_truenas.py", text)
            self.assertNotIn("install.sh", text)

    def test_manifest_en_formulier_dekken_dezelfde_kerndiensten(self):
        manifest = yaml.safe_load(MANIFEST.read_text())
        poort, = manifest["network"]["ports"]
        parameters = {p["name"]: p for p in manifest["parameters"]}
        self.assertEqual(manifest["install"]["method"], "custom_app")
        self.assertEqual((poort["protocol"], poort["container_port"]), ("tcp", 8080))
        self.assertEqual(parameters[poort["host_port"]["param"]]["default"], 8088)
        self.assertFalse(manifest["network"]["host_network"])
        self.assertEqual(manifest["privileges"], {"privileged": False, "devices": [], "cap_add": [], "gpu": "none"})
        self.assertEqual(manifest["healthcheck"]["type"], "builtin")
        self.assertEqual(manifest["healthcheck"]["monitor"]["pad"], "/readyz")
        self.assertEqual(manifest["healthcheck"]["monitor"]["verwachte_status"], 200)
        opslag, = manifest["storage"]
        self.assertEqual(opslag["mount"], "/appdata")
        koppeling, = manifest["custom_app"]["storage"]
        self.assertEqual(koppeling["name"], opslag["name"])
        dataset = parameters[koppeling["host_path"]["param"]]
        self.assertEqual(dataset["source"], "dataset_picker")
        self.assertEqual(dataset["dataset_role"], opslag["required_dataset_role"])
        self.assertEqual(manifest["backup"]["snapshot_datasets"], [opslag["name"]])
        self.assertEqual(manifest["backup"]["db_export"]["method"], "pg_dump")
        for doel in ("rpo", "rto"):
            parameter = parameters[manifest["backup"][doel]["param"]]
            self.assertEqual((parameter["type"], parameter["decision"]), ("duration", "D16"))
            self.assertNotIn("default", parameter)
        self.assertFalse(manifest["upgrade"]["tag_only_rollback_safe"])
        formulier = FORMULIER.read_text()
        for veld in (manifest["app_id"], manifest["image"]["repository"], "8080", "8088", "/appdata", "/readyz",
                     "shc_allinone.startpad", "#offline-terugvalkopie-voorbereiden"):
            self.assertIn(veld, formulier)

    def test_hostpaden_gebruiken_een_poolplaatshouder(self):
        for pad in (FORMULIER, MANIFEST):
            self.assertNotRegex(pad.read_text(), r"/mnt/(?!<POOL>/)")
        self.assertIn("/mnt/<POOL>/", FORMULIER.read_text())

    def test_stick_is_optioneel_en_heeft_alleen_een_formulierveld(self):
        formulier = FORMULIER.read_text()
        self.assertEqual(
            set(re.findall(r"/dev/[^\s`]+", formulier)), {"/dev/serial/by-id/<NAAM-VAN-JE-STICK>", "/dev/zigbee"}
        )
        self.assertNotIn("/dev/", MANIFEST.read_text())
        for veld in ("Zigbee-stick (alleen als je er een hebt)", "SHC_ZIGBEE_ADAPTER", "ember",
                     "NIET GEVALIDEERD (echte NAS)", "NIET GEVALIDEERD (hardware)"):
            self.assertIn(veld, formulier)
        self.assertNotRegex(formulier, r"(?im)^`{3}(sh|bash)|\bls\s+-")

    def test_ontwikkelcompose_gebruikt_de_bestaande_proefstack(self):
        ontwikkel = ROOT / "deploy/dev/compose.yaml"
        voorbeeld = ROOT / ".env.example"
        self.assertIn("geen installatieweg", ontwikkel.read_text().splitlines()[0].lower())
        self.assertIn("ontwikkeling", voorbeeld.read_text().splitlines()[0].lower())
        opdracht = ["docker", "compose", "--env-file", str(voorbeeld), "-f", str(ontwikkel),
                    "config", "--format", "json"]
        env = dict(os.environ, SHC_HTTP_PORT="18088", SHC_ROOT="/proefopslag")
        proces = subprocess.run(opdracht, env=env, capture_output=True, text=True, check=True)  # noqa: S603
        diensten = json.loads(proces.stdout)["services"]
        self.assertEqual(diensten["web"]["ports"][0]["published"], "18088")
        self.assertIn("/proefopslag/postgres", [v["source"] for v in diensten["db"]["volumes"]])
        self.assertNotIn("zigbee2mqtt", diensten)

    def test_runtime_gebruikt_vaste_pakketbronnen(self):
        dockerfile = (ROOT / "deploy/allinone/Dockerfile").read_text()
        self.assertNotIn("apt.postgresql.org/pub/repos/apt", dockerfile)
        self.assertRegex(dockerfile, r"snapshot\.debian\.org/archive/debian/\d{8}T\d{6}Z")
        self.assertRegex(dockerfile, r"snapshot\.debian\.org/archive/debian-security/\d{8}T\d{6}Z")

    def test_standaardformulier_leidt_niet_naar_een_tunnelupdate(self):
        readme = (ROOT / "README.md").read_text()
        quickstart = readme.split("## Snel starten\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("(docs/installatie-truenas.md)", quickstart)
        self.assertNotIn("(docs/buiten-huis.md)", quickstart)
        development = readme.split("## Alleen voor ontwikkeling\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("(docs/buiten-huis.md)", development)

        guide = (ROOT / "docs/installatie-truenas.md").read_text()
        install = guide.split("## Android-app", 1)[0].lower()
        self.assertIn("apps › discover apps › custom app", install)
        self.assertIn("nieuwe vaste tag in het formulier", install)
        self.assertNotRegex(install, r"yaml|render_truenas|install\.sh|sudo|docker compose")

        tunnel = (ROOT / "docs/buiten-huis.md").read_text()
        intro = tunnel.split("\n## ", 1)[0]
        self.assertIn("lokale Compose-omgeving", intro)
        self.assertIn("(installatie-truenas.md)", intro)
        self.assertNotIn("werk de app bij in TrueNAS", tunnel)
