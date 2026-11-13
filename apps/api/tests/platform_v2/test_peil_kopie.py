"""Pin- en drifttest van de Peil-kopie (ONTWERPTAAL §14, E08; C1.3-ALG-05; O-14).

- **offline** (``tools/peil_kopie.py controleer``): elke kopie in ``design/ontwerptaal/`` heeft een geldige herkomstkop
  en de gepinde sha256; de kopieën in de web-app zijn gelijk aan de gepinde kopie; ``PeilTokens.kt`` (Android) is uit
  de gepinde ``tokens.json`` gemaakt;
- **tegen een bron** (tijdelijke git-repo, dus ook in CI): kopie ≠ bron op de gepinde commit → fout; dezelfde versie met
  andere inhoud in de huidige bron → fout (drift); een nieuwere versie → alleen een waarschuwing;
- **tegen de echte bron** als ``ai-server-manager`` naast deze repo staat (``MH_PEIL_BRON``); anders overgeslagen. In CI
  doet ``tools/peil_kopie.py vergelijk --online`` dat tegen GitHub.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import peil_kopie as pk
import pytest


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def kopie(tmp_path: Path, repo: Path) -> Path:
    """Een losse kopie van alles wat de controle leest, om fouten in te zetten."""
    doel = tmp_path / "kopie"
    shutil.copytree(repo / pk.MAP, doel / pk.MAP)
    for pad in [*pk.APP_KOPIEEN, pk.PEIL_TOKENS_KT]:
        (doel / pad).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / pad, doel / pad)
    return doel


@pytest.fixture
def bron(tmp_path: Path, repo: Path) -> tuple[Path, str]:
    """Een tijdelijke git-repo met de gepinde bronbestanden op hun bronpad."""
    b = tmp_path / "bron"
    for naam, spec in pk.BESTANDEN.items():
        (b / spec.bron_pad).parent.mkdir(parents=True, exist_ok=True)
        (b / spec.bron_pad).write_bytes(pk.inhoud_van(repo, naam))
    _git(b, "init", "-q")
    _git(b, "add", ".")
    _git(b, "commit", "-q", "-m", "bron")
    return b, _git(b, "rev-parse", "HEAD")


@pytest.fixture
def doel(tmp_path: Path, repo: Path, bron: tuple[Path, str]) -> Path:
    """Kopieën die ``bijwerken`` uit de tijdelijke bron schrijft (plus PeilTokens.kt: zelfde tokens.json)."""
    d = tmp_path / "doel"
    (d / pk.PEIL_TOKENS_KT).parent.mkdir(parents=True)
    shutil.copy2(repo / pk.PEIL_TOKENS_KT, d / pk.PEIL_TOKENS_KT)
    assert pk.bijwerken(d, bron[0], bron[1], "werkbranch") == []
    return d


# ------------------------------------------------------------------------------------------------ offline
def test_de_kopie_in_de_repo_klopt(repo: Path) -> None:
    assert pk.controleer(repo) == []


def test_pin_is_peil_1_1_1(repo: Path) -> None:
    herkomst = json.loads((repo / pk.HERKOMST).read_text(encoding="utf-8"))
    assert herkomst["versie"] == "1.1.1"
    assert herkomst["bron_repo"] == "lvos04/ai-server-manager"
    assert set(herkomst["bestanden"]) == set(pk.BESTANDEN)
    tokens = json.loads((repo / pk.MAP / "tokens.json").read_text(encoding="utf-8"))
    assert tokens["$extensions"]["nl.peil"]["versie"] == "1.1.1"


@pytest.mark.parametrize("naam", ["ontwerptaal.css", "aliaslagen/my_house.css", "ONTWERPTAAL.md", "tokens.json"])
def test_een_gewijzigde_kopie_is_drift(kopie: Path, naam: str) -> None:
    pad = kopie / pk.MAP / naam
    pad.write_bytes(pad.read_bytes().replace(b"1", b"2", 1) if naam == "tokens.json" else pad.read_bytes() + b"\n")
    assert any(f"{naam}: inhoud gewijzigd" in f or f"{naam}: herkomstkop" in f for f in pk.controleer(kopie))


def test_app_kopie_met_de_hand_gewijzigd(kopie: Path) -> None:
    pad = kopie / "apps/web/src/peil-alias.css"
    pad.write_text(pad.read_text(encoding="utf-8").replace("#FFC3A4", "#F4A57E"), encoding="utf-8")
    assert any("peil-alias.css: inhoud ≠ de gepinde kopie" in f for f in pk.controleer(kopie))


def test_kop_van_een_andere_versie(kopie: Path) -> None:
    pad = kopie / pk.MAP / "COMPONENTEN.md"
    pad.write_text(
        pad.read_text(encoding="utf-8").replace("HERKOMST — Peil 1.1.1", "HERKOMST — Peil 1.1.0", 1), encoding="utf-8"
    )
    assert any("COMPONENTEN.md: herkomstkop wijkt af" in f for f in pk.controleer(kopie))


def test_kop_ontbreekt(kopie: Path) -> None:
    pad = kopie / pk.MAP / "ontwerptaal.css"
    pad.write_bytes(pk.inhoud_van(kopie, "ontwerptaal.css"))
    assert any("geen geldige herkomstkop" in f for f in pk.controleer(kopie))


def test_android_tokens_uit_een_andere_tokens_json(kopie: Path) -> None:
    pad = kopie / pk.PEIL_TOKENS_KT
    herkomst = json.loads((kopie / pk.HERKOMST).read_text(encoding="utf-8"))
    pad.write_text(
        pad.read_text(encoding="utf-8").replace(herkomst["bestanden"]["tokens.json"]["sha256"], "0" * 64),
        encoding="utf-8",
    )
    assert any("PeilTokens.kt: niet gegenereerd uit de gepinde tokens.json" in f for f in pk.controleer(kopie))


def test_pin_onvolledig_of_onleesbaar(kopie: Path) -> None:
    h = json.loads((kopie / pk.HERKOMST).read_text(encoding="utf-8"))
    del h["bestanden"]["components.json"]
    (kopie / pk.HERKOMST).write_text(json.dumps(h), encoding="utf-8")
    assert any("'bestanden' moet precies" in f for f in pk.controleer(kopie))
    (kopie / pk.HERKOMST).write_text("[", encoding="utf-8")
    assert any("onleesbaar" in f for f in pk.controleer(kopie))


# ------------------------------------------------------------------------------------------------ tegen een bron (E08)
def test_bijwerken_schrijft_dezelfde_kopie_als_in_de_repo(repo: Path, doel: Path) -> None:
    for naam in pk.BESTANDEN:
        assert pk.inhoud_van(doel, naam) == pk.inhoud_van(repo, naam)
    for app in pk.APP_KOPIEEN:
        assert (doel / app).read_bytes().split(b"\n", 2)[2] == (repo / app).read_bytes().split(b"\n", 2)[2]


def test_gelijk_aan_de_bron(doel: Path, bron: tuple[Path, str]) -> None:
    assert pk.vergelijk(doel, bron[0], online=False) == ([], [])


def test_zelfde_versie_met_andere_inhoud_in_de_bron_is_drift(doel: Path, bron: tuple[Path, str]) -> None:
    pad = bron[0] / pk.BESTANDEN["COMPONENTEN.md"].bron_pad
    pad.write_bytes(pad.read_bytes() + b"\nStil gewijzigd zonder nieuwe versie.\n")
    fouten, _ = pk.vergelijk(doel, bron[0], online=False)
    assert any("COMPONENTEN.md: Peil 1.1.1 in de bron heeft andere inhoud" in f for f in fouten), fouten


def test_zonder_eigen_versie_telt_de_versie_van_de_set(doel: Path, bron: tuple[Path, str]) -> None:
    pad = bron[0] / pk.BESTANDEN["components.json"].bron_pad
    pad.write_bytes(pad.read_bytes().replace(b"{", b"{ ", 1))
    fouten, _ = pk.vergelijk(doel, bron[0], online=False)
    assert any("components.json: Peil 1.1.1 in de bron heeft andere inhoud" in f for f in fouten), fouten


def test_nieuwere_versie_in_de_bron_is_een_waarschuwing(doel: Path, bron: tuple[Path, str]) -> None:
    pad = bron[0] / pk.BESTANDEN["tokens.json"].bron_pad
    pad.write_bytes(pad.read_bytes().replace(b'"versie": "1.1.1"', b'"versie": "1.2.0"'))
    fouten, waarschuwingen = pk.vergelijk(doel, bron[0], online=False)
    assert fouten == []
    assert any("de bron heeft Peil 1.2.0 (kopie 1.1.1)" in w for w in waarschuwingen), waarschuwingen


def test_kopie_anders_dan_de_gepinde_commit(doel: Path, bron: tuple[Path, str]) -> None:
    # Kopie én pin samen veranderd: de offline controle ziet niets, de vergelijking met de bron wel.
    pad = doel / pk.MAP / "components.json"
    pad.write_bytes(pad.read_bytes() + b"\n")
    h = json.loads((doel / pk.HERKOMST).read_text(encoding="utf-8"))
    h["bestanden"]["components.json"]["sha256"] = pk.sha256(pad.read_bytes())
    (doel / pk.HERKOMST).write_text(json.dumps(h), encoding="utf-8")
    assert pk.controleer(doel) == []
    fouten, _ = pk.vergelijk(doel, bron[0], online=False)
    assert any("components.json: kopie ≠ bron op de gepinde commit" in f for f in fouten), fouten


def test_gepinde_commit_onbekend(doel: Path, bron: tuple[Path, str]) -> None:
    h = json.loads((doel / pk.HERKOMST).read_text(encoding="utf-8"))
    h["bron_commit"] = "f" * 40
    (doel / pk.HERKOMST).write_text(json.dumps(h), encoding="utf-8")
    fouten, _ = pk.vergelijk(doel, bron[0], online=False)
    assert fouten  # herkomstkoppen ≠ pin, of de commit is niet te lezen: nooit stil "in orde"


# ------------------------------------------------------------------------------------------------ echte bron
def test_tegen_de_echte_bron(repo: Path) -> None:
    kandidaat = os.environ.get("MH_PEIL_BRON")
    bron_map = Path(kandidaat) if kandidaat else repo.parent / "ai-server-manager"
    if not (bron_map / ".git").exists():
        pytest.skip("ai-server-manager staat niet naast deze repo (zet MH_PEIL_BRON); CI vergelijkt online")
    herkomst = json.loads((repo / pk.HERKOMST).read_text(encoding="utf-8"))
    for naam, spec in pk.BESTANDEN.items():
        assert pk.haal_lokaal(bron_map, spec.bron_pad, herkomst["bron_commit"]) == pk.inhoud_van(repo, naam), naam


def test_herkomst_md_noemt_versie_commit_en_elke_sha256(repo: Path) -> None:
    herkomst = json.loads((repo / pk.HERKOMST).read_text(encoding="utf-8"))
    tekst = (repo / pk.MAP / "HERKOMST.md").read_text(encoding="utf-8")
    assert f"**Peil {herkomst['versie']}**" in tekst
    assert herkomst["bron_commit"] in tekst
    for naam, pin in herkomst["bestanden"].items():
        assert pin["sha256"] in tekst, naam
