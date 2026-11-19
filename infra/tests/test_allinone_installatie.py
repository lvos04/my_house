"""De installatieroute en de herkomst van de runtimepakketten."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Installatie(unittest.TestCase):
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
