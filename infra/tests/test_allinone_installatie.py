"""De installatieroute en de herkomst van de runtimepakketten."""

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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

    def test_formulier_is_de_installatieroute(self):
        for name in ("README.md", "docs/installatie-truenas.md"):
            sections = (ROOT / name).read_text().lower().split("\n## ")
            text = "\n".join(s for s in sections if not s.startswith("alleen voor ontwikkeling\n"))
            self.assertIn("standaard custom app", text)
            self.assertNotIn("install via yaml", text)
            self.assertNotIn("render_truenas.py --", text)

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
