"""Drifttest van de contractkopie (contract 1.3, kop en §10; C1.3-ALG-01, C1.3-MH-01).

Drie lagen:

1. **altijd** (ook in CI zonder bronrepo): beide kopieën hebben exact de sha256 die ``CONTRACT_HERKOMST.json`` pint, en
   versie en bestandsnaam passen bij de pin (``tools/contract_kopie.py controleer``);
2. **tegen een bron**: de kopie is byte-gelijk aan de bron op de gepinde commit; een latere wijziging van de bron is
   alleen een waarschuwing (hier getoetst met een tijdelijke git-repo als bron, dus ook in CI);
3. **tegen de echte bron** als ``ai-server-manager`` naast deze repo staat (of ``MH_CONTRACT_BRON`` ernaar wijst). Staat
   die er niet, dan wordt alleen deze laag overgeslagen — nooit als geslaagd gemeld. In CI doet de stap
   ``tools/contract_kopie.py vergelijk --online`` hetzelfde tegen GitHub.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import contract_kopie as ck
import pytest

VERWACHTE_VERSIE = "1.3"


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
    """Een losse kopie van docs/platform-v2 (alleen de contractbestanden) om fouten in te zetten."""
    doel = tmp_path / "kopie"
    (doel / ck.MAP).mkdir(parents=True)
    for naam in ("PLATFORM_CONTRACTEN.md", f"CONTRACT_{VERWACHTE_VERSIE}_CONFORMITEIT.md", "CONTRACT_HERKOMST.json"):
        shutil.copy2(repo / ck.MAP / naam, doel / ck.MAP / naam)
    return doel


@pytest.fixture
def bron(tmp_path: Path, repo: Path) -> tuple[Path, str]:
    """Een tijdelijke git-repo met dezelfde bronbestanden als de kopie (de 'bron' op een commit)."""
    b = tmp_path / "bron"
    (b / ck.BRON_MAP).mkdir(parents=True)
    for naam in ("PLATFORM_CONTRACTEN.md", f"CONTRACT_{VERWACHTE_VERSIE}_CONFORMITEIT.md"):
        shutil.copy2(repo / ck.MAP / naam, b / ck.BRON_MAP / naam)
    _git(b, "init", "-q")
    _git(b, "add", ".")
    _git(b, "commit", "-q", "-m", "bron")
    return b, _git(b, "rev-parse", "HEAD")


# ------------------------------------------------------------------------------------------------ laag 1
def test_kopie_heeft_de_gepinde_sha256(repo: Path) -> None:
    assert ck.controleer(repo) == []


def test_kopie_is_contract_1_3_met_conformiteitslijst(repo: Path) -> None:
    herkomst = json.loads((repo / ck.HERKOMST).read_text(encoding="utf-8"))
    assert herkomst["versie"] == VERWACHTE_VERSIE
    assert herkomst["bron_repo"] == "lvos04/ai-server-manager"
    contract = (repo / ck.MAP / "PLATFORM_CONTRACTEN.md").read_bytes()
    assert b"Versie: `contracts 1.3`" in contract
    lijst = (repo / ck.MAP / "CONTRACT_1.3_CONFORMITEIT.md").read_text(encoding="utf-8")
    assert "## 8. my_house" in lijst and "C1.3-MH-07" in lijst


def test_een_gewijzigde_byte_is_drift(kopie: Path) -> None:
    pad = kopie / ck.MAP / "PLATFORM_CONTRACTEN.md"
    data = bytearray(pad.read_bytes())
    data[-2] ^= 0x01
    pad.write_bytes(bytes(data))
    fouten = ck.controleer(kopie)
    assert any("PLATFORM_CONTRACTEN.md: wijkt af van de bron" in f for f in fouten), fouten


def test_een_gerepareerde_link_is_ook_drift(kopie: Path) -> None:
    """'Een repo past een kopie nooit aan, ook niet om een link te repareren' (contract, kop)."""
    pad = kopie / ck.MAP / "CONTRACT_1.3_CONFORMITEIT.md"
    pad.write_text(pad.read_text(encoding="utf-8").replace("`PLATFORM_CONTRACTEN.md`", "[x](PLATFORM_CONTRACTEN.md)", 1),
                   encoding="utf-8")
    assert any("CONTRACT_1.3_CONFORMITEIT.md: wijkt af" in f for f in ck.controleer(kopie))


def test_pin_en_versie_moeten_kloppen(kopie: Path) -> None:
    pin = kopie / ck.HERKOMST
    h = json.loads(pin.read_text(encoding="utf-8"))
    h["versie"] = "1.2"
    pin.write_text(json.dumps(h), encoding="utf-8")
    assert any("'bestanden' moet precies" in f for f in ck.controleer(kopie))
    h["versie"] = "1.3"
    h["bron_commit"] = "02b119c"
    pin.write_text(json.dumps(h), encoding="utf-8")
    assert any("volledige commit-hash" in f for f in ck.controleer(kopie))


def test_ontbrekende_kopie_of_onleesbare_pin(kopie: Path) -> None:
    (kopie / ck.MAP / "CONTRACT_1.3_CONFORMITEIT.md").unlink()
    assert any("ontbreekt" in f for f in ck.controleer(kopie))
    (kopie / ck.HERKOMST).write_text("{", encoding="utf-8")
    assert any("onleesbaar" in f for f in ck.controleer(kopie))


# ------------------------------------------------------------------------------------------------ laag 2
def test_bijwerken_en_vergelijken_met_een_bron(tmp_path: Path, bron: tuple[Path, str]) -> None:
    bron_map, commit = bron
    doel = tmp_path / "doel"
    assert ck.bijwerken(doel, bron_map, commit, "werkbranch") == []
    assert ck.vergelijk(doel, bron_map, online=False) == ([], [])

    # De bron verandert binnen dezelfde versie na de gepinde commit: waarschuwing, geen fout (vaste bron-commit).
    contract = bron_map / ck.BRON_MAP / "PLATFORM_CONTRACTEN.md"
    contract.write_bytes(contract.read_bytes() + b"\nnieuwe regel\n")
    fouten, waarschuwingen = ck.vergelijk(doel, bron_map, online=False)
    assert fouten == []
    assert any("de bron is na" in w for w in waarschuwingen), waarschuwingen

    # Een nieuwere versie in de bron: waarschuwing met de opdracht om bij te werken.
    contract.write_bytes(contract.read_bytes().replace(b"`contracts 1.3`", b"`contracts 1.4`", 1))
    fouten, waarschuwingen = ck.vergelijk(doel, bron_map, online=False)
    assert fouten == [] and any("contract 1.4" in w for w in waarschuwingen), waarschuwingen


def test_kopie_anders_dan_de_gepinde_commit_is_een_fout(tmp_path: Path, bron: tuple[Path, str]) -> None:
    bron_map, commit = bron
    doel = tmp_path / "doel"
    assert ck.bijwerken(doel, bron_map, commit, None) == []
    # Pin en kopie samen veranderen (de offline controle slaagt dan) — de vergelijking met de bron vangt het.
    pad = doel / ck.MAP / "PLATFORM_CONTRACTEN.md"
    pad.write_bytes(pad.read_bytes() + b"\n")
    h = json.loads((doel / ck.HERKOMST).read_text(encoding="utf-8"))
    h["bestanden"]["PLATFORM_CONTRACTEN.md"]["sha256"] = ck.sha256(pad.read_bytes())
    (doel / ck.HERKOMST).write_text(json.dumps(h), encoding="utf-8")
    assert ck.controleer(doel) == []
    fouten, _ = ck.vergelijk(doel, bron_map, online=False)
    assert any("kopie ≠ bron op de gepinde commit" in f for f in fouten), fouten


def test_onbekende_bron_commit_is_een_fout(tmp_path: Path, bron: tuple[Path, str]) -> None:
    bron_map, commit = bron
    doel = tmp_path / "doel"
    assert ck.bijwerken(doel, bron_map, commit, None) == []
    h = json.loads((doel / ck.HERKOMST).read_text(encoding="utf-8"))
    h["bron_commit"] = "0" * 40
    (doel / ck.HERKOMST).write_text(json.dumps(h), encoding="utf-8")
    fouten, _ = ck.vergelijk(doel, bron_map, online=False)
    assert fouten and all("niet te lezen" in f for f in fouten), fouten
    assert ck.bijwerken(doel, bron_map, "main", None) == ["--commit moet een volledige commit-hash (40 hex) zijn"]


# ------------------------------------------------------------------------------------------------ laag 3
def _echte_bron(repo: Path) -> Path | None:
    kandidaat = os.environ.get("MH_CONTRACT_BRON")
    pad = Path(kandidaat) if kandidaat else repo.parent / "ai-server-manager"
    return pad if (pad / ".git").exists() else None


def test_kopie_is_byte_gelijk_aan_de_echte_bron_op_de_gepinde_commit(repo: Path) -> None:
    bron_map = _echte_bron(repo)
    if bron_map is None:
        pytest.skip("ai-server-manager staat niet naast deze repo (zet MH_CONTRACT_BRON); CI vergelijkt online")
    fouten, _ = ck.vergelijk(repo, bron_map, online=False)
    assert fouten == []
