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
