#!/usr/bin/env python3
"""Android-afdrukken (Peil) uit het Paparazzi-rapport halen en de lange delen samenvoegen.

Gebruik (vereist Pillow):
    PEIL_VOLLEDIG=1 ./gradlew :app:testDebugUnitTest --tests 'nl.huis.smarthome.peil.*'
    python verzamel.py apps/android/app/build/reports/paparazzi/debug <ruwe map>
    python nabewerk.py <ruwe map> docs/screenshots/peil/android     # inkorten en verkleinen

Het rapport bevat per afdruk een runs/<id>.js met de naam (<scherm>-<thema>-<breedte>) en het
PNG-bestand. "<scherm>-volledig-deel0..2-<thema>-<breedte>" worden onder elkaar gezet tot
"<scherm>-volledig-<thema>-<breedte>.png", zoals bij de nulmeting (voor/android).
"""

import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image


def main() -> int:
    rapport, doel = Path(sys.argv[1]), Path(sys.argv[2])
    doel.mkdir(parents=True, exist_ok=True)
    afdrukken: dict[str, Path] = {}
    for js in sorted((rapport / "runs").glob("*.js")):
        tekst = js.read_text(encoding="utf-8")
        for item in json.loads(tekst[tekst.index("= [") + 2 :].rstrip().rstrip(";")):
            afdrukken[item["name"]] = rapport / item["file"]  # de laatste run wint

    delen: dict[str, dict[int, Path]] = {}
    for naam, bron in sorted(afdrukken.items()):
        m = re.fullmatch(r"(.+-volledig)-deel(\d)-(.+)", naam)
        if m:
            delen.setdefault(f"{m.group(1)}-{m.group(3)}", {})[int(m.group(2))] = bron
        else:
            shutil.copyfile(bron, doel / f"{naam}.png")
    for naam, stukken in sorted(delen.items()):
        beelden = [Image.open(stukken[k]).convert("RGB") for k in sorted(stukken)]
        uit = Image.new("RGB", (beelden[0].width, sum(b.height for b in beelden)))
        y = 0
        for b in beelden:
            uit.paste(b, (0, y))
            y += b.height
        uit.save(doel / f"{naam}.png")
    print(f"{len(afdrukken) - sum(len(v) for v in delen.values())} afdrukken + {len(delen)} samengevoegd → {doel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
