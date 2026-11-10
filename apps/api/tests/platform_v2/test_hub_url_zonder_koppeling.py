"""Contract 1.3 §2.3 regels 2 en 5, §3.2 (C1.3-MH-02, C1.3-MH-07): in my_house is ``HUB_URL`` alleen het doel van de
link "Terug naar de Hub".

Statische bewaking over de broncode (geen database, geen netwerk):

- nergens in de app (API, worker, adapters, web-app, startscripts, Android, compose/installatie) een enrollment, een
  enrollment-token, een sleutelset-vingerafdruk, een koppelbestand of een push naar de Hub-inbox: een bestaande app krijgt
  in v1 geen enrollment en ``HUB_URL`` start er nooit een;
- het startscript van de webcontainer leest uit de omgeving alleen ``HUB_URL`` (en ``SHC_CONFIG_DIR`` voor de tests),
  dus nooit een token of vingerafdruk die een installatie via de Hub zou kunnen meegeven;
- de web-app haalt het Hub-adres alleen uit ``/_shc/config.json``: geen ``document.referrer``, geen ``location`` en geen
  queryparameters in ``src/hub.ts`` (nooit afleiden uit ``Referer``, de URL of andere invoer).

De controles staan in losse functies, zodat de negatieve gevallen onderaan dezelfde code toetsen als de echte bron.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]

#: Broncode die in een installatie draait of hem maakt (tests, documentatie en de contractkopie tellen niet mee).
BRONMAPPEN = (
    "apps/api/src",
    "integrations/src",
    "apps/web/src",
    "apps/web/docker",
    "apps/android/app/src/main",
    "apps/android/core/src/main",
    "infra/compose",
    "infra/scripts",
)
BRONBESTANDEN = ("apps/web/nginx.conf.template", "apps/web/Dockerfile", "apps/api/Dockerfile")
EXTENSIES = {".py", ".ts", ".tsx", ".sh", ".kt", ".kts", ".yaml", ".yml", ".conf", ".template", ".xml", ""}
#: Wat alleen bij een koppeling met de Hub hoort (contract §3.2–§3.5, §7): in my_house v1 nergens.
VERBODEN = re.compile(
    r"HUB_ENROLLMENT_TOKEN|ENROLLMENT_TOKEN|HUB_KEY_FINGERPRINT|/services/enroll|/events/inbox|hub-link\.json"
    r"|services/self",
)
STARTSCRIPT = REPO / "apps/web/docker/15-shc-config.sh"
HUB_TS = REPO / "apps/web/src/hub.ts"
TOEGESTAAN_IN_STARTSCRIPT = {"HUB_URL", "SHC_CONFIG_DIR"}


def bronbestanden(root: Path = REPO) -> list[Path]:
    uit: list[Path] = []
    for map_ in BRONMAPPEN:
        for pad in sorted((root / map_).rglob("*")):
            if not pad.is_file() or pad.suffix not in EXTENSIES or "__pycache__" in pad.parts:
                continue
            if "test" in pad.relative_to(root / map_).parts:  # apps/web/src/test: tests, geen bron
                continue
            uit.append(pad)
    uit.extend(root / naam for naam in BRONBESTANDEN if (root / naam).is_file())
    return uit


def koppelsporen(bestanden: list[Path], root: Path = REPO) -> list[str]:
    """Elke plek die naar enrollment, een Hub-token of de Hub-inbox wijst (leeg = in orde)."""
    uit: list[str] = []
    for pad in bestanden:
        tekst = pad.read_text(encoding="utf-8", errors="replace")
        for nr, regel in enumerate(tekst.splitlines(), 1):
            if VERBODEN.search(regel):
                uit.append(f"{pad.relative_to(root)}:{nr}: {regel.strip()[:120]}")
    return uit


def omgeving_in_startscript(tekst: str) -> set[str]:
    """Namen die het script uit de omgeving leest: shellvariabelen die het niet zelf zet, plus ENVIRON[...] in awk."""
    zonder_commentaar = "\n".join(r for r in tekst.splitlines() if not r.lstrip().startswith("#"))
    gezet = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", zonder_commentaar, re.M))
    gezet |= set(re.findall(r"\|\|\s*([A-Za-z_][A-Za-z0-9_]*)=", zonder_commentaar))
    gelezen = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", zonder_commentaar))
    environ = set(re.findall(r"ENVIRON\[\"([^\"]+)\"\]", zonder_commentaar))
    # Een omgevingstoewijzing vóór een opdracht (HUB_URL="$HUB_URL" awk ...) leest de waarde uit de omgeving.
    voor_opdracht = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)=\"\$\1\"", zonder_commentaar))
    return ((gelezen - gezet) | environ | voor_opdracht) - {"0", "1", "2", "@", "#", "?"}


def adresbronnen_in_hub_ts(tekst: str) -> list[str]:
    """Bronnen voor het Hub-adres buiten /_shc/config.json (commentaar telt niet mee)."""
    code = re.sub(r"/\*.*?\*/", "", tekst, flags=re.S)  # blokcommentaar
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)  # regelcommentaar
    code = re.sub(r"\s//\s.*$", "", code, flags=re.M)  # commentaar achter code ("https://" in een tekst blijft staan)
    verboden = (r"document\.referrer", r"\blocation\b", r"URLSearchParams", r"localStorage", r"sessionStorage")
    return [p for p in verboden if re.search(p, code)]


def test_nergens_een_koppeling_met_de_hub() -> None:
    bestanden = bronbestanden()
    assert len(bestanden) > 100, "de bronmappen zijn niet gevonden"
    assert STARTSCRIPT in bestanden and HUB_TS in bestanden
    assert koppelsporen(bestanden) == []


def test_startscript_leest_alleen_hub_url() -> None:
    gelezen = omgeving_in_startscript(STARTSCRIPT.read_text(encoding="utf-8"))
    assert "HUB_URL" in gelezen
    assert gelezen <= TOEGESTAAN_IN_STARTSCRIPT, gelezen - TOEGESTAAN_IN_STARTSCRIPT


def test_web_app_haalt_het_adres_alleen_uit_de_configuratie() -> None:
    tekst = HUB_TS.read_text(encoding="utf-8")
    assert "HUB_CONFIG_URL = '/_shc/config.json'" in tekst
    assert adresbronnen_in_hub_ts(tekst) == []


# ------------------------------------------------------------------------------------------------ negatieve gevallen
@pytest.mark.parametrize(
    "regel",
    [
        'token = os.environ["HUB_ENROLLMENT_TOKEN"]',
        "ENROLLMENT_TOKEN=${ENROLLMENT_TOKEN:-}",
        "fp = getenv('HUB_KEY_FINGERPRINT')",
        'await client.post(f"{hub}/api/v1/services/enroll", json=body)',
        "url = hub_url + '/api/v1/events/inbox'",
        'LINK = Path("/appdata/secrets/hub-link.json")',
    ],
)
def test_een_koppelspoor_wordt_gevonden(tmp_path: Path, regel: str) -> None:
    bron = tmp_path / "apps/api/src/shc_api"
    bron.mkdir(parents=True)
    (bron / "hub.py").write_text(f"# bestaande code\n{regel}\n", encoding="utf-8")
    assert koppelsporen(bronbestanden(tmp_path), tmp_path) == [f"apps/api/src/shc_api/hub.py:2: {regel}"]


def test_tests_en_documentatie_tellen_niet_mee(tmp_path: Path) -> None:
    (tmp_path / "apps/web/src/test").mkdir(parents=True)
    (tmp_path / "apps/web/src/test/x.test.ts").write_text("// HUB_ENROLLMENT_TOKEN\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/x.md").write_text("HUB_ENROLLMENT_TOKEN\n", encoding="utf-8")
    assert koppelsporen(bronbestanden(tmp_path), tmp_path) == []


@pytest.mark.parametrize(
    ("toevoeging", "extra"),
    [
        ('if [ -n "${HUB_ENROLLMENT_TOKEN:-}" ]; then :; fi', "HUB_ENROLLMENT_TOKEN"),
        ('fp="$HUB_KEY_FINGERPRINT"', "HUB_KEY_FINGERPRINT"),
        ('x="$(awk \'BEGIN { print ENVIRON["HUB_TOKEN"] }\')"', "HUB_TOKEN"),
    ],
)
def test_startscript_met_een_extra_omgevingsvariabele(toevoeging: str, extra: str) -> None:
    tekst = STARTSCRIPT.read_text(encoding="utf-8") + "\n" + toevoeging + "\n"
    assert extra in omgeving_in_startscript(tekst) - TOEGESTAAN_IN_STARTSCRIPT


@pytest.mark.parametrize(
    "toevoeging",
    [
        "const vanReferer = document.referrer;",
        "const vanUrl = new URLSearchParams(window.location.search).get('hub');",
        "const terug = location.origin;",
        "const bewaard = localStorage.getItem('hub_url');",
    ],
)
def test_hub_ts_met_een_andere_adresbron(toevoeging: str) -> None:
    tekst = HUB_TS.read_text(encoding="utf-8") + "\n" + toevoeging + "\n"
    assert adresbronnen_in_hub_ts(tekst) != []
