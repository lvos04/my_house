#!/usr/bin/env python3
"""Versie-gepinde kopie van de ontwerptaal Peil bewaken (ONTWERPTAAL §14, pin- en driftregel E08; O-14).

De bron van waarheid is ``lvos04/ai-server-manager`` (``docs/platform-v2/ONTWERPTAAL.md``, ``design/ontwerptaal/`` en
``docs/adr/ADR-0015-ontwerptaal-peil.md``). my_house heeft drie soorten kopieën:

1. ``design/ontwerptaal/`` — de gepinde kopie. ``.css``/``.md`` dragen een **herkomstkop van twee regels** met versie,
   bron-commit, bronpad en de sha256 van het bronbestand; de rest is byte-gelijk aan de bron. ``tokens.json`` en
   ``components.json`` kunnen geen kop dragen en zijn byte-gelijk.
2. Kopieën in de web-app (``apps/web/src/peil-alias.css``, ``apps/web/src/peil/ontwerptaal.css``): zelfde kopformaat,
   inhoud gelijk aan de gepinde kopie (``src/test/peil-herkomst.test.ts`` toetst dat ook in de webtests).
3. ``PeilTokens.kt`` van de Android-app, gegenereerd uit ``tokens.json``; de kop noemt de sha256 van ``tokens.json``
   (``apps/android/tools/peil_tokens.py --check`` controleert elke waarde).

``design/ontwerptaal/HERKOMST.json`` pint versie, bron-commit en sha256 per bestand.

Opdrachten (alleen de standaardbibliotheek):

``controleer``
    Offline (CI, elke run; ook in de unit-tests): koppen, inhoud, pins, app-kopieën en de kop van PeilTokens.kt.
``vergelijk --bron <checkout van ai-server-manager>`` en/of ``vergelijk --online``
    Tegen de bron (E08): kopie ≠ bron op de gepinde commit → **fout**; in de huidige bron (bronbranch online,
    werkmap lokaal) dezelfde versie met andere inhoud → **fout** (drift); een nieuwere versie → **waarschuwing**
    (bijwerken is een bewuste stap met de eigen tests, geen rode CI). Onleesbare bron: fout voor de gepinde commit,
    waarschuwing voor de huidige stand — nooit stil "in orde".
``bijwerken --bron <checkout> --commit <40 hex> [--ref <branch>]``
    Alle kopieën (1 en 2) en ``HERKOMST.json`` opnieuw schrijven vanuit die checkout op die commit. Daarna:
    ``python3 apps/android/tools/peil_tokens.py`` (3) en de web-, browser- en Android-tests.

Uitgang 0 = in orde (eventueel met waarschuwingen), 1 = drift of fout, 2 = verkeerd gebruik.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
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
MAP = Path("design") / "ontwerptaal"
HERKOMST = MAP / "HERKOMST.json"
PEIL_TOKENS_KT = Path("apps/android/app/src/main/java/nl/huis/smarthome/ui/theme/PeilTokens.kt")


@dataclass(frozen=True)
class Bestand:
    bron_pad: str
    kop: str | None  # "css", "md" of None (geen kop: byte-gelijk)
    versie: re.Pattern[bytes] | None  # versie zoals de bron hem zelf noemt (begin van het bestand)


BESTANDEN: dict[str, Bestand] = {
    "ontwerptaal.css": Bestand(
        "design/ontwerptaal/ontwerptaal.css", "css", re.compile(rb"Peil (\d+\.\d+\.\d+) \xe2\x80\x94 ontwerptaal")
    ),
    "aliaslagen/my_house.css": Bestand(
        "design/ontwerptaal/aliaslagen/my_house.css",
        "css",
        re.compile(rb"^/\* Peil (\d+\.\d+\.\d+) \xe2\x80\x94 alias"),
    ),
    "tokens.json": Bestand("design/ontwerptaal/tokens.json", None, re.compile(rb'"versie": "(\d+\.\d+\.\d+)"')),
    "components.json": Bestand("design/ontwerptaal/components.json", None, None),
    "ONTWERPTAAL.md": Bestand("docs/platform-v2/ONTWERPTAAL.md", "md", re.compile(rb"versie \*\*(\d+\.\d+\.\d+)\*\*")),
    "COMPONENTEN.md": Bestand(
        "design/ontwerptaal/COMPONENTEN.md", "md", re.compile(rb"^Versie (\d+\.\d+\.\d+) ", re.M)
    ),
    "ADR-ONTWERPTAAL.md": Bestand(
        "docs/adr/ADR-0015-ontwerptaal-peil.md", "md", re.compile(rb"\*\*Versie ontwerptaal:\*\* (\d+\.\d+\.\d+)")
    ),
}
#: kopieën in de web-app: pad → (bestand in design/ontwerptaal, tweede kopregel)
APP_KOPIEEN: dict[str, tuple[str, str]] = {
    "apps/web/src/peil-alias.css": (
        "aliaslagen/my_house.css",
        "Kopie in de app (fase 1, docs/ontwerp/PEIL_MIGRATIE.md §8.1), "
        "geïmporteerd door src/peil/peil.css (laag legacy).",
    ),
    "apps/web/src/peil/ontwerptaal.css": (
        "ontwerptaal.css",
        "Kopie in de app (fase 2, docs/ontwerp/PEIL_MIGRATIE.md §8.2), geïmporteerd door src/peil/peil.css.",
    ),
}
KOP = re.compile(
    r"^(?:/\*|<!--) HERKOMST — Peil (?P<versie>\d+\.\d+\.\d+) · "
    r"bron (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}) "
    r"(?P<pad>\S+) · sha256 van het bronbestand \(zonder deze kop\): (?P<sha>[0-9a-f]{64})(?: |$)"
)


def maak_kop(soort: str, versie: str, commit: str, bron_pad: str, digest: str, toelichting: str | None = None) -> str:
    """De twee kopregels (zonder afsluitende regelovergang na de tweede regel)."""
    eerste = (
        f"HERKOMST — Peil {versie} · bron {BRON_REPO}@{commit} {bron_pad} · "
        f"sha256 van het bronbestand (zonder deze kop): {digest}"
    )
    if soort == "md":
        return (
            f"<!-- {eerste} · versie-gepinde kopie (O-14), niet met de hand wijzigen (tools/peil_kopie.py); relatieve "
            "links wijzen naar de mappen van de bronrepo. -->\n"
        )
    tweede = toelichting or "Versie-gepinde kopie (O-14, ONTWERPTAAL §14)."
    return (
        f"/* {eerste}\n   {tweede} Niet met de hand wijzigen: bijwerken met tools/peil_kopie.py "
        "(design/ontwerptaal/HERKOMST.md). */"
    )


def splits(data: bytes) -> tuple[re.Match[str] | None, bytes]:
    """Herkomstkop (eerste regel) en de bronbytes (alles na de tweede regel)."""
    eerste, _, rest = data.partition(b"\n")
    _, _, inhoud = rest.partition(b"\n")
    return KOP.match(eerste.decode("utf-8", "replace")), inhoud


def bronversie(naam: str, data: bytes) -> str | None:
    patroon = BESTANDEN[naam].versie
    if patroon is None:
        return None
    m = patroon.search(data[:3000])
    return m.group(1).decode() if m else None


def lees_herkomst(root: Path) -> dict[str, object]:
    data = json.loads((root / HERKOMST).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("geen object")
    return data


def inhoud_van(root: Path, naam: str) -> bytes:
    """De bronbytes van een gepinde kopie (zonder kop)."""
    data = (root / MAP / naam).read_bytes()
    return data if BESTANDEN[naam].kop is None else splits(data)[1]


# ------------------------------------------------------------------------------------------------ controleer
def controleer(root: Path = REPO_ROOT) -> list[str]:
    """Offline controle; geeft de lijst met fouten (leeg = in orde)."""
    try:
        herkomst = lees_herkomst(root)
    except (OSError, ValueError) as exc:
        return [f"{HERKOMST}: onleesbaar ({exc})"]
    fouten: list[str] = []
    versie, commit, pins = herkomst.get("versie"), herkomst.get("bron_commit"), herkomst.get("bestanden")
    if not isinstance(versie, str) or not re.fullmatch(r"\d+\.\d+\.\d+", versie):
        return [f"{HERKOMST}: 'versie' moet de vorm 1.1.1 hebben"]
    if herkomst.get("bron_repo") != BRON_REPO:
        fouten.append(f"{HERKOMST}: bron_repo moet {BRON_REPO} zijn")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        return [*fouten, f"{HERKOMST}: bron_commit moet een volledige commit-hash (40 hex) zijn"]
    if not isinstance(pins, dict) or set(pins) != set(BESTANDEN):
        return [*fouten, f"{HERKOMST}: 'bestanden' moet precies {', '.join(sorted(BESTANDEN))} bevatten"]
    gepind: dict[str, str] = {}
    for naam, spec in BESTANDEN.items():
        pin = pins[naam]
        if not isinstance(pin, dict) or pin.get("bron_pad") != spec.bron_pad:
            fouten.append(f"{HERKOMST}: {naam} moet bron_pad {spec.bron_pad} hebben")
            continue
        try:
            data = (root / MAP / naam).read_bytes()
        except OSError as exc:
            fouten.append(f"{MAP / naam}: ontbreekt ({exc.strerror})")
            continue
        if spec.kop is None:
            inhoud = data
        else:
            kop, inhoud = splits(data)
            if kop is None:
                fouten.append(f"{MAP / naam}: de eerste regel is geen geldige herkomstkop")
                continue
            if (kop["versie"], kop["repo"], kop["commit"], kop["pad"], kop["sha"]) != (
                versie,
                BRON_REPO,
                commit,
                spec.bron_pad,
                pin.get("sha256"),
            ):
                fouten.append(f"{MAP / naam}: herkomstkop wijkt af van HERKOMST.json (versie, bron, pad of sha256)")
        werkelijk = sha256(inhoud)
        if werkelijk != pin.get("sha256"):
            fouten.append(
                f"{MAP / naam}: inhoud gewijzigd (sha256 {werkelijk[:16]}… ≠ gepind {str(pin.get('sha256'))[:16]}…); "
                "nooit met de hand wijzigen, bijwerken met 'tools/peil_kopie.py bijwerken'"
            )
        v = bronversie(naam, inhoud)
        if spec.versie is not None and v != versie:
            fouten.append(f"{MAP / naam}: de inhoud noemt Peil {v}, de pin {versie}")
        gepind[naam] = str(pin.get("sha256"))
    for app, (naam, _) in APP_KOPIEEN.items():
        try:
            data = (root / app).read_bytes()
        except OSError as exc:
            fouten.append(f"{app}: ontbreekt ({exc.strerror})")
            continue
        kop, inhoud = splits(data)
        if kop is None:
            fouten.append(f"{app}: de eerste regel is geen geldige herkomstkop")
            continue
        if naam in gepind and (kop["versie"], kop["commit"], kop["sha"]) != (versie, commit, gepind[naam]):
            fouten.append(f"{app}: herkomstkop wijkt af van HERKOMST.json")
        if naam in gepind and sha256(inhoud) != gepind[naam]:
            fouten.append(f"{app}: inhoud ≠ de gepinde kopie {MAP / naam} (niet met de hand wijzigen)")
    if "tokens.json" in gepind:
        try:
            kt = (root / PEIL_TOKENS_KT).read_text(encoding="utf-8")[:2000]
        except OSError as exc:
            fouten.append(f"{PEIL_TOKENS_KT}: ontbreekt ({exc.strerror})")
        else:
            if gepind["tokens.json"] not in kt:
                fouten.append(
                    f"{PEIL_TOKENS_KT}: niet gegenereerd uit de gepinde tokens.json "
                    "(python3 apps/android/tools/peil_tokens.py)"
                )
    return fouten


# ------------------------------------------------------------------------------------------------ vergelijk
def vergelijk(root: Path, bron_map: Path | None, online: bool) -> tuple[list[str], list[str]]:
    """Vergelijk met de bron (E08); geeft (fouten, waarschuwingen)."""
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

        nu_ref = ref if soort == "online" else None
        try:
            nu_set = bronversie("tokens.json", haal(nu_ref, BESTANDEN["tokens.json"].bron_pad))
        except (NietGevonden, Onbereikbaar):
            nu_set = None
        for naam, spec in BESTANDEN.items():
            kopie = inhoud_van(root, naam)
            try:
                gepind = haal(commit, spec.bron_pad)
            except (NietGevonden, Onbereikbaar) as exc:
                fouten.append(f"{soort} {naam}: gepinde commit {commit[:7]} niet te lezen ({exc})")
                continue
            if gepind != kopie:
                fouten.append(f"{soort} {naam}: kopie ≠ bron op de gepinde commit {commit[:7]} (drift)")
                continue
            if soort == "online" and not ref:
                waarschuwingen.append(f"{soort} {naam}: geen bron_ref; de huidige bron is niet vergeleken")
                continue
            try:
                nu = haal(nu_ref, spec.bron_pad)
            except (NietGevonden, Onbereikbaar) as exc:
                waarschuwingen.append(
                    f"{soort} {naam}: huidige bron niet te lezen ({exc}); alleen de gepinde commit gecontroleerd"
                )
                continue
            if nu == kopie:
                continue
            nu_versie = bronversie(naam, nu) or nu_set
            if nu_versie is None:
                fouten.append(f"{soort} {naam}: de bron heeft geen leesbare Peil-versie meer (drift)")
            elif semver(nu_versie) > semver(versie):
                waarschuwingen.append(
                    f"{soort} {naam}: de bron heeft Peil {nu_versie} (kopie {versie}); bewust bijwerken met "
                    "'tools/peil_kopie.py bijwerken' en de eigen tests"
                )
            else:
                fouten.append(f"{soort} {naam}: Peil {nu_versie} in de bron heeft andere inhoud dan de kopie (drift)")
    return fouten, waarschuwingen


# ------------------------------------------------------------------------------------------------ bijwerken
def bijwerken(root: Path, bron_map: Path, commit: str, ref: str | None) -> list[str]:
    if not COMMIT.fullmatch(commit):
        return ["--commit moet een volledige commit-hash (40 hex) zijn"]
    inhoud: dict[str, bytes] = {}
    for naam, spec in BESTANDEN.items():
        try:
            inhoud[naam] = haal_lokaal(bron_map, spec.bron_pad, commit)
        except NietGevonden as exc:
            return [str(exc)]
    versies = {v for naam in BESTANDEN if (v := bronversie(naam, inhoud[naam])) is not None}
    if len(versies) != 1:
        return [f"de bronbestanden noemen verschillende of geen Peil-versies: {sorted(versies)}"]
    versie = versies.pop()
    pins: dict[str, dict[str, str]] = {}
    for naam, spec in BESTANDEN.items():
        digest = sha256(inhoud[naam])
        pins[naam] = {"bron_pad": spec.bron_pad, "sha256": digest}
        pad = root / MAP / naam
        pad.parent.mkdir(parents=True, exist_ok=True)
        if spec.kop is None:
            pad.write_bytes(inhoud[naam])
        else:
            kop = maak_kop(spec.kop, versie, commit, spec.bron_pad, digest)
            pad.write_bytes(kop.encode("utf-8") + b"\n" + inhoud[naam])
    for app, (naam, toelichting) in APP_KOPIEEN.items():
        spec = BESTANDEN[naam]
        kop = maak_kop("css", versie, commit, spec.bron_pad, pins[naam]["sha256"], toelichting)
        (root / app).parent.mkdir(parents=True, exist_ok=True)
        (root / app).write_bytes(kop.encode("utf-8") + b"\n" + inhoud[naam])
    herkomst = {
        "ontwerptaal": "Peil",
        "versie": versie,
        "bron_repo": BRON_REPO,
        "bron_commit": commit,
        "bron_ref": ref or "",
        "bestanden": pins,
        "app_kopieen": {app: f"{MAP.as_posix()}/{naam}" for app, (naam, _) in APP_KOPIEEN.items()},
        "android": f"{PEIL_TOKENS_KT.as_posix()} (gegenereerd uit tokens.json: apps/android/tools/peil_tokens.py)",
        "toelichting": "Versie-gepinde kopie (O-14, ONTWERPTAAL §14, E08). Bijwerken alleen met 'python3 "
        "tools/peil_kopie.py bijwerken', daarna peil_tokens.py en de web-, browser- en Android-tests.",
    }
    (root / HERKOMST).write_text(json.dumps(herkomst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return [f for f in controleer(root) if not f.startswith(str(PEIL_TOKENS_KT))]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Versie-gepinde kopie van de ontwerptaal Peil bewaken (O-14, E08).")
    sub = p.add_subparsers(dest="opdracht", required=True)
    sub.add_parser("controleer", help="offline: koppen, inhoud, pins, app-kopieën en PeilTokens.kt")
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
    meld("warning", "Peil", waarschuwingen)
    meld("error", "Peil", fouten)
    if not fouten:
        print(f"Peil-kopie in orde ({args.opdracht}).")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
