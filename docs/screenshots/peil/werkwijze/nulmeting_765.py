"""Herhaal de Peil-nulmeting met een vaste webbron, afdrukwerkwijze en schermselectie."""

import argparse
import itertools
import json
import os
import subprocess
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

BRON = "c014d76a660f15d9dff094bacf2064e8dc36c455"
HULPMIDDELEN = "4a97255cab9eb6445c8461747b2be8f2ee027cd1"
WERKWIJZE = "docs/screenshots/peil/werkwijze"
SCHERMEN = (
    "overzicht", "klimaat-bediening", "klimaat-uit-conflict", "klimaat-weekschema",
    "verlichting", "energie", "optimalisatie", "apparaten", "systeem", "account-mijn",
    "installatie", "overzicht-verouderd", "klimaat-verouderd",
)
THEMAS = ("licht", "donker")
BREEDTES = (390, 768, 1440)


def controleer(uitvoer):
    verwacht = Counter(itertools.product(SCHERMEN, THEMAS, BREEDTES))
    verwacht["forced-colors", "licht", 390] = 1
    runs = json.loads((uitvoer / "review.json").read_text())["runs"]
    gevonden = Counter((r["scherm"], r["thema"], r["breedte"]) for r in runs)
    if gevonden != verwacht:
        raise SystemExit(f"Selectie wijkt af: {sum((verwacht-gevonden).values())} ontbrekend, "
                         f"{sum((gevonden-verwacht).values())} extra.")
    print("78 schermmetingen en één afzonderlijke systeemkleurproef aanwezig.")


def meet(uitvoer):
    repo = Path(os.environ["PEIL_REPO"]).resolve()
    git = ["git", "-C", str(repo)]
    if subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip() != BRON:
        raise SystemExit(f"Gebruik een aparte proefkloon op {BRON}.")
    subprocess.run([*git, "diff", "--quiet", "HEAD", "--", "apps/web", "apps/api", "integrations"], check=True)
    if not os.environ.get("PEIL_PG"):
        raise SystemExit("Geef de afzonderlijke lokale proefdatabase op met PEIL_PG.")
    uitvoer.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=uitvoer) as werk:
        archief = Path(werk) / "hulpmiddelen.tar"
        with archief.open("wb") as f:
            subprocess.run([*git, "archive", HULPMIDDELEN, WERKWIJZE], stdout=f, check=True)
        with tarfile.open(archief) as tar:
            tar.extractall(werk, filter="data")
        env = {
            **os.environ, "PEIL_REPO": str(repo), "PEIL_SCRIPT": "review.mjs", "PEIL_SKIP_BUILD": "0",
            "PEIL_ALLEEN": ",".join(SCHERMEN), "PEIL_THEMAS": ",".join(THEMAS),
            "PEIL_WIDTHS": ",".join(map(str, BREEDTES)),
            "PYTHONPATH": os.pathsep.join([str(repo / "apps/api/src"), str(repo / "integrations/src"),
                                           os.environ.get("PYTHONPATH", "")]),
        }
        subprocess.run(["bash", Path(werk) / WERKWIJZE / "run_shots.sh", uitvoer], env=env, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uitvoer", type=Path)
    parser.add_argument("--controleer", action="store_true", help="toets een eerder geschreven review.json")
    args = parser.parse_args()
    uitvoer = args.uitvoer.resolve()
    if not args.controleer:
        meet(uitvoer)
    controleer(uitvoer)
