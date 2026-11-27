"""Adresfixtures in de integratieproeven blijven lokaal en bevatten geen letterlijke netwerkadressen."""

import re
import unittest
from ipaddress import IPv4Address
from pathlib import Path


class Proefadressen(unittest.TestCase):
    def test_proeven_gebruiken_eigen_adressen(self):
        root = Path(__file__).resolve().parents[2]
        letterlijk = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
        locaties = []
        bestanden = [*root.glob("integrations/tests/*.py"), *root.glob("apps/api/tests/*.py")]
        for pad in sorted(bestanden):
            for nummer, regel in enumerate(pad.read_text().splitlines(), 1):
                for waarde in letterlijk.findall(regel):
                    try:
                        adres = IPv4Address(waarde)
                    except ValueError:
                        continue
                    if not adres.is_loopback and not adres.is_unspecified:
                        locaties.append(f"{pad.name}:{nummer}")
                        break
        self.assertEqual(locaties, [], "Gebruik een gegenereerd proefadres of een testhost.")
