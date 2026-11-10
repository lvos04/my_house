#!/usr/bin/env python3
"""Byte-gelijke kopie van het platformcontract bewaken (contract 1.3, kop en §10; C1.3-ALG-01, C1.3-MH-01).

``docs/platform-v2/PLATFORM_CONTRACTEN.md`` en ``docs/platform-v2/CONTRACT_<versie>_CONFORMITEIT.md`` zijn
**byte-gelijk** aan de bron in ``lvos04/ai-server-manager`` op een vaste bron-commit. ``CONTRACT_HERKOMST.json``
(zelfde map) pint de versie, de bron-commit en de sha256 van beide bestanden. Een kopie wordt nooit lokaal aangepast,
ook niet om een link te repareren; een nieuwe kopie overnemen is een stap van de orkestratie (``bijwerken``).

Opdrachten (alleen de standaardbibliotheek):

``controleer``
    Offline (CI, elke run; ook in de unit-tests): beide kopieën hebben exact de gepinde sha256, de versie in het
    contract en de naam van de conformiteitslijst passen bij de pin.
``vergelijk --bron <checkout van ai-server-manager>`` en/of ``vergelijk --online``
    Tegen de bron. De kopie moet byte-gelijk zijn aan de bron **op de gepinde commit** (anders: drift, fout). Is de
    bron daarna veranderd (bronbranch online, werkmap lokaal), dan is dat een **waarschuwing**: het contract laat de
    kopie de vaste bron-commit volgen; overnemen is een bewuste stap. Een bron die niet te lezen is, is een fout
    (gepinde commit) of een waarschuwing (huidige stand) — nooit stil "in orde".
``bijwerken --bron <checkout> --commit <40 hex> [--ref <branch>]``
    Kopieën en ``CONTRACT_HERKOMST.json`` opnieuw schrijven vanuit die checkout op die commit.

Uitgang 0 = in orde (eventueel met waarschuwingen), 1 = drift of fout, 2 = verkeerd gebruik.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kopie_bron import (  # noqa: E402 - pad hierboven
    BRON_REPO,
    COMMIT,
    NietGevonden,
    Onbereikbaar,
    haal_lokaal,
    haal_online,
    meld,
    semver,
    sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAP = Path("docs") / "platform-v2"
HERKOMST = MAP / "CONTRACT_HERKOMST.json"
BRON_MAP = "docs/platform-v2"
CONTRACT = "PLATFORM_CONTRACTEN.md"
VERSIE_IN_CONTRACT = re.compile(rb"^Versie: `contracts (\d+\.\d+)`", re.M)


def conformiteit(versie: str) -> str:
    return f"CONTRACT_{versie}_CONFORMITEIT.md"


def contractversie(data: bytes) -> str | None:
    m = VERSIE_IN_CONTRACT.search(data[:4000])
    return m.group(1).decode() if m else None


def lees_herkomst(root: Path) -> dict[str, object]:
    data = json.loads((root / HERKOMST).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("geen object")
    return data


# ------------------------------------------------------------------------------------------------ controleer
def controleer(root: Path = REPO_ROOT) -> list[str]:
    """Offline controle; geeft de lijst met fouten (leeg = in orde)."""
    try:
        herkomst = lees_herkomst(root)
    except (OSError, ValueError) as exc:
        return [f"{HERKOMST}: onleesbaar ({exc})"]
    fouten: list[str] = []
    versie = herkomst.get("versie")
    commit = herkomst.get("bron_commit")
    bestanden = herkomst.get("bestanden")
    if not isinstance(versie, str) or not re.fullmatch(r"\d+\.\d+", versie):
        return [f"{HERKOMST}: 'versie' moet de vorm 1.3 hebben"]
    if herkomst.get("bron_repo") != BRON_REPO:
        fouten.append(f"{HERKOMST}: bron_repo moet {BRON_REPO} zijn")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        fouten.append(f"{HERKOMST}: bron_commit moet een volledige commit-hash (40 hex) zijn")
    verwacht = {CONTRACT, conformiteit(versie)}
    if not isinstance(bestanden, dict) or set(bestanden) != verwacht:
        return [*fouten, f"{HERKOMST}: 'bestanden' moet precies {', '.join(sorted(verwacht))} bevatten"]
    for naam in sorted(verwacht):
        pin = bestanden[naam]
        if not isinstance(pin, dict) or pin.get("bron_pad") != f"{BRON_MAP}/{naam}":
            fouten.append(f"{HERKOMST}: {naam} moet bron_pad {BRON_MAP}/{naam} hebben")
            continue
        try:
            data = (root / MAP / naam).read_bytes()
        except OSError as exc:
            fouten.append(f"{naam}: ontbreekt ({exc.strerror})")
            continue
        werkelijk = sha256(data)
        if werkelijk != pin.get("sha256"):
            fouten.append(
                f"{naam}: wijkt af van de bron (sha256 {werkelijk[:16]}… ≠ gepind {str(pin.get('sha256'))[:16]}…); "
                "nooit lokaal aanpassen, opnieuw kopiëren met 'tools/contract_kopie.py bijwerken'"
            )
        if naam == CONTRACT and contractversie(data) != versie:
            fouten.append(f"{naam}: versie in het contract ({contractversie(data)}) ≠ gepinde versie {versie}")
        if naam != CONTRACT and f"contract {versie}".encode() not in data.split(b"\n", 1)[0]:
            fouten.append(f"{naam}: de eerste regel noemt contract {versie} niet")
    return fouten


# ------------------------------------------------------------------------------------------------ vergelijk
def vergelijk(root: Path, bron_map: Path | None, online: bool) -> tuple[list[str], list[str]]:
    """Vergelijk met de bron; geeft (fouten, waarschuwingen)."""
    fouten = controleer(root)
    waarschuwingen: list[str] = []
    if fouten:
        return fouten, waarschuwingen
    herkomst = lees_herkomst(root)
    versie, commit = str(herkomst["versie"]), str(herkomst["bron_commit"])
    ref = str(herkomst.get("bron_ref") or "")
    bronnen: list[tuple[str, Path | None]] = []
    if bron_map is not None:
        bronnen.append(("lokaal", bron_map))
    if online:
        bronnen.append(("online", None))
    for soort, waar in bronnen:

        def haal(r: str | None, pad: str, waar: Path | None = waar) -> bytes:
            if waar is None:
                return haal_online(pad, r or commit)
            return haal_lokaal(waar, pad, r)

        for naam in (CONTRACT, conformiteit(versie)):
            pad = f"{BRON_MAP}/{naam}"
            kopie = (root / MAP / naam).read_bytes()
            try:
                gepind = haal(commit, pad)
            except (NietGevonden, Onbereikbaar) as exc:
                fouten.append(f"{soort} {naam}: gepinde bron-commit {commit[:7]} niet te lezen ({exc})")
                continue
            if gepind != kopie:
                fouten.append(f"{soort} {naam}: kopie ≠ bron op de gepinde commit {commit[:7]} (drift)")
                continue
            # De huidige stand van de bron: online de bronbranch, lokaal de werkmap. Alleen ter informatie.
            if soort == "online" and not ref:
                continue
            try:
                nu = haal(ref if soort == "online" else None, pad)
            except (NietGevonden, Onbereikbaar) as exc:
                waarschuwingen.append(
                    f"{soort} {naam}: huidige bron niet te lezen ({exc}); alleen de gepinde commit gecontroleerd"
                )
                continue
            if nu == kopie:
                continue
            try:
                nu_contract = haal(ref if soort == "online" else None, f"{BRON_MAP}/{CONTRACT}")
            except (NietGevonden, Onbereikbaar):
                nu_contract = b""
            nu_versie = contractversie(nu_contract)
            if nu_versie and semver(nu_versie) > semver(versie):
                waarschuwingen.append(
                    f"{soort} {naam}: de bron heeft contract {nu_versie} (kopie {versie}); overnemen is een stap van "
                    "de orkestratie ('tools/contract_kopie.py bijwerken')"
                )
            else:
                waarschuwingen.append(
                    f"{soort} {naam}: de bron is na {commit[:7]} gewijzigd (contract {nu_versie or '?'}); de kopie "
                    "volgt de vaste bron-commit tot de orkestratie een nieuwe kopie overneemt"
                )
    return fouten, waarschuwingen


# ------------------------------------------------------------------------------------------------ bijwerken
def bijwerken(root: Path, bron_map: Path, commit: str, ref: str | None) -> list[str]:
    if not COMMIT.fullmatch(commit):
        return ["--commit moet een volledige commit-hash (40 hex) zijn"]
    try:
        contract = haal_lokaal(bron_map, f"{BRON_MAP}/{CONTRACT}", commit)
    except NietGevonden as exc:
        return [str(exc)]
    versie = contractversie(contract)
    if versie is None:
        return [f"{CONTRACT}: geen versieregel in de bron op {commit[:7]}"]
    try:
        lijst = haal_lokaal(bron_map, f"{BRON_MAP}/{conformiteit(versie)}", commit)
    except NietGevonden as exc:
        return [str(exc)]
    (root / MAP).mkdir(parents=True, exist_ok=True)
    pins: dict[str, dict[str, str]] = {}
    for naam, data in ((CONTRACT, contract), (conformiteit(versie), lijst)):
        (root / MAP / naam).write_bytes(data)
        pins[naam] = {"bron_pad": f"{BRON_MAP}/{naam}", "sha256": sha256(data)}
    herkomst = {
        "versie": versie,
        "bron_repo": BRON_REPO,
        "bron_commit": commit,
        "bron_ref": ref or "",
        "bestanden": pins,
        "toelichting": "Byte-gelijke kopie van het platformcontract (contract kop en §10). Nooit lokaal aanpassen; "
        "overnemen met 'python3 tools/contract_kopie.py bijwerken' (stap van de orkestratie).",
    }
    (root / HERKOMST).write_text(json.dumps(herkomst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return controleer(root)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Byte-gelijke kopie van het platformcontract bewaken.")
    sub = p.add_subparsers(dest="opdracht", required=True)
    sub.add_parser("controleer", help="offline: gepinde sha256, versie en naam van de conformiteitslijst")
    v = sub.add_parser("vergelijk", help="tegen de bron (lokale checkout en/of GitHub)")
    v.add_argument("--bron", type=Path, help="map van een checkout van ai-server-manager")
    v.add_argument("--online", action="store_true", help="tegen GitHub: gepinde commit en bronbranch")
    b = sub.add_parser("bijwerken", help="kopieën opnieuw schrijven vanuit een checkout")
    b.add_argument("--bron", type=Path, required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--ref")
    args = p.parse_args(argv)
    waarschuwingen: list[str] = []
    if args.opdracht == "controleer":
        fouten = controleer()
    elif args.opdracht == "vergelijk":
        if args.bron is None and not args.online:
            p.error("geef --bron en/of --online")
        fouten, waarschuwingen = vergelijk(REPO_ROOT, args.bron, args.online)
    else:
        fouten = bijwerken(REPO_ROOT, args.bron, args.commit, args.ref)
    meld("warning", "Contract", waarschuwingen)
    meld("error", "Contract", fouten)
    if not fouten:
        print(f"Contractkopie in orde ({args.opdracht}).")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
