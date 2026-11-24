"""Gedeelde hulp voor de versie-gepinde kopieën uit ``ai-server-manager`` (de bron van waarheid).

Gebruikt door ``tools/contract_kopie.py`` (platformcontract, contract 1.3 kop en §10) en ``tools/peil_kopie.py``
(ontwerptaal Peil, ONTWERPTAAL §14, E08). Alleen de standaardbibliotheek: de hulpmiddelen draaien in CI vóór er iets
geïnstalleerd is en lokaal zonder venv.

Een bronbestand wordt gelezen

- **lokaal** uit een checkout van ai-server-manager (``git show <ref>:<pad>``, of de werkmap als er geen ref is), of
- **online** van Forgejo voor het platformcontract (FORGEJO_TOKEN of forgejo/token in de gebruikersconfiguratie)
  en van de publieke GitHub-bron voor Peil, met vier herhalingen bij een netwerkfout.

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
import urllib.parse
import urllib.request
from pathlib import Path

BRON_REPO = "lvos04/ai-server-manager"
CONTRACT_REPO = "leon/ai-server-manager"
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


def forgejo_verzoek(repo: str, ref: str, pad: str) -> urllib.request.Request:
    """Lees de privébron met de ingestelde Forgejo-leestoegang, ook buiten CI."""
    url = (
        f"https://git.medialeon.nl/api/v1/repos/{urllib.parse.quote(repo)}/raw/{urllib.parse.quote(pad)}"
        f"?{urllib.parse.urlencode({'ref': ref})}"
    )
    verzoek = urllib.request.Request(url, headers={"User-Agent": "platform-contractcontrole/1.0"})  # noqa: S310
    token = os.environ.get("FORGEJO_TOKEN")
    if not token:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        tokenbestand = config / "forgejo" / "token"
        if tokenbestand.is_file():
            token = tokenbestand.read_text(encoding="utf-8").strip()
    if token:
        verzoek.add_unredirected_header("Authorization", f"token {token}")
    return verzoek


def haal_online(
    pad: str, ref: str, *, bron_repo: str = BRON_REPO, wachttijden: tuple[int, ...] = (0, 2, 4, 8, 16)
) -> bytes:
    """Bronbestand op de gevraagde commit of branch."""
    if bron_repo == CONTRACT_REPO:
        verzoek = forgejo_verzoek(bron_repo, ref, pad)
    else:
        url = RAW.format(repo=BRON_REPO, ref=ref, pad=pad)
        verzoek = urllib.request.Request(url)  # noqa: S310 - vaste Peil-bron
    laatste: Exception | None = None
    for wacht in wachttijden:
        if wacht:
            time.sleep(wacht)
        try:
            with urllib.request.urlopen(verzoek, timeout=30) as resp:  # noqa: S310 - vaste bronnen
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NietGevonden(f"{verzoek.full_url}: 404") from exc
            if exc.code in (401, 403):
                raise Onbereikbaar(f"{verzoek.full_url}: leestoegang geweigerd (HTTP {exc.code})") from exc
            laatste = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            laatste = exc
    raise Onbereikbaar(f"{verzoek.full_url}: niet op te halen ({laatste})")


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
