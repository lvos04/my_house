"""De herstelproef toetst ook tabellen buiten de vaste proefinrichting."""

import unittest

from allinone_herstelproef import EXPECTED, Proeffout, check_inventory


class Inventaris(unittest.TestCase):
    def test_inhoud_en_beide_tellingen_tegen_archief(self):
        archief = dict(EXPECTED, integrations=1)
        inventaris = {t: {"source": n, "target": n, "same": True} for t, n in archief.items()}
        resultaat = {"restored_at": "proef", "inventory": inventaris}
        check_inventory(resultaat, archief)
        for bron, doel, gelijk in ((1, 0, True), (0, 0, True), (0, 1, True), (1, 1, False), (True, 1, True)):
            with self.subTest(bron=bron, doel=doel, gelijk=gelijk):
                inventaris["integrations"] = {"source": bron, "target": doel, "same": gelijk}
                with self.assertRaises(Proeffout):
                    check_inventory(resultaat, archief)
