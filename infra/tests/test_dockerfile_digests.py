"""Proeven voor de verplichte controle van externe basisimages."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_dockerfile_digests import violations  # noqa: E402

DIGEST = "sha256:" + "ab" * 32


class DockerfileDigests(unittest.TestCase):
    def test_digest_platform_en_interne_stages(self):
        dockerfile = (
            f"from --platform=$BUILDPLATFORM \\\n    registry.example/base:3@{DIGEST} AS basis\n"
            "FROM BASIS as tweede\n"
            "FROM scratch\n"
        )
        self.assertEqual(violations(dockerfile), [])

    def test_ongepinde_externe_image(self):
        dockerfile = "# FROM ongelezen:1\nFROM registry.example/base:3 AS basis\nFROM basis\n"
        self.assertEqual(violations(dockerfile), [(2, "registry.example/base:3")])

    def test_heredoc_is_geen_dockerfile_instructie(self):
        dockerfile = "FROM scratch\nCOPY <<'TEKST' /bestand\nFROM letterlijk:1\nTEKST\nFROM ongepind:1\n"
        self.assertEqual(violations(dockerfile), [(5, "ongepind:1")])

    def test_gewone_shelltekst_verbergt_geen_volgende_stage(self):
        dockerfile = "FROM scratch\nRUN echo 'tekst <<REGEL tekst'\nFROM ongepind:1\n"
        self.assertEqual(violations(dockerfile), [(3, "ongepind:1")])

    def test_weigert_onvolledige_digest_en_veranderlijke_basis(self):
        dockerfile = "FROM basis:3@sha256:1234\nFROM ${BASIS}\nFROM volgende\nFROM scratch AS volgende\n"
        self.assertEqual(violations(dockerfile), [(1, "basis:3@sha256:1234"), (2, "${BASIS}"), (3, "volgende")])
