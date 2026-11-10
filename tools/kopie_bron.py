"""Gedeelde hulp voor de versie-gepinde kopieën uit ``lvos04/ai-server-manager`` (de bron van waarheid).

Gebruikt door ``tools/contract_kopie.py`` (platformcontract, contract 1.3 kop en §10) en ``tools/peil_kopie.py``
(ontwerptaal Peil, ONTWERPTAAL §14, E08). Alleen de standaardbibliotheek: de hulpmiddelen draaien in CI vóór er iets
geïnstalleerd is en lokaal zonder venv.

Een bronbestand wordt gelezen

- **lokaal** uit een checkout van ai-server-manager (``git show <ref>:<pad>``, of de werkmap als er geen ref is), of
- **online** van GitHub (``raw.githubusercontent.com``; de bronrepo is publiek, er is geen token nodig), met vier
  herhalingen na 2/4/8/16 s bij een netwerkfout.

Een bestand dat niet te lezen is, geeft een uitzondering; de aanroeper meldt dat als fout of waarschuwing, nooit als
"in orde".
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BRON_REPO = "lvos04/ai-server-manager"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{pad}"
COMMIT = re.compile(r"[0-9a-f]{40}")


class NietGevonden(FileNotFoundError):
    """Het bestand bestaat niet op die ref (404 of ``git show`` faalt)."""


class Onbereikbaar(ConnectionError):
    """De bron is niet te bereiken (netwerk), ook niet na de herhalingen."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semver(versie: str) -> tuple[int, ...]:
    """``"1.1.1"`` → ``(1, 1, 1)``; ``"1.3"`` → ``(1, 3)``."""
    return tuple(int(x) for x in versie.split("."))


def haal_online(pad: str, ref: str, *, wachttijden: tuple[int, ...] = (0, 2, 4, 8, 16)) -> bytes:
    """Bronbestand van GitHub op ``ref`` (commit of branch)."""
    url = RAW.format(repo=BRON_REPO, ref=ref, pad=pad)
    laatste: Exception | None = None
    for wacht in wachttijden:
        if wacht:
            time.sleep(wacht)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - vaste https-URL naar GitHub
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NietGevonden(f"{url}: 404") from exc
            laatste = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            laatste = exc
    raise Onbereikbaar(f"{url}: niet op te halen ({laatste})")


def haal_lokaal(bron_map: Path, pad: str, ref: str | None) -> bytes:
    """Bronbestand uit een lokale checkout: op ``ref`` via git, of uit de werkmap als ``ref`` ``None`` is."""
    if ref is None:
        try:
            return (bron_map / pad).read_bytes()
        except OSError as exc:
            raise NietGevonden(f"{bron_map / pad}: {exc.strerror}") from exc
    out = subprocess.run(  # noqa: S603 - vaste opdracht zonder shell
        ["git", "-C", str(bron_map), "show", f"{ref}:{pad}"],  # noqa: S607 - git uit PATH, zoals elke ontwikkelaar
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        raise NietGevonden(f"{bron_map}: {ref}:{pad} niet gevonden")
    return out.stdout


def in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def meld(soort: str, onderwerp: str, regels: list[str]) -> None:
    """Fouten en waarschuwingen; in GitHub Actions als annotatie."""
    for regel in regels:
        if in_ci():
            print(f"::{soort}::{onderwerp}: {regel}")
        else:
            print(f"{'fout' if soort == 'error' else 'waarschuwing'}: {onderwerp}: {regel}")
