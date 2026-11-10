"""Contract 1.3 §11: ``docs/platform-v2/EVIDENCE.json`` volgt de vaste Excel-statussen en validatiewaarden (C1.3-MH-01,
C1.3-ALG-02).

- ``status`` uit de lijst van het blad: Werkpakketten (``B06-…``, contractpunten, reviews)
  ``Niet gestart|Bezig|Gereed|Geblokkeerd|N.v.t.``, Testmatrix (``EX..``, ``JLT-..``) ook ``Mislukt``. ``Gereed`` en
  ``Geslaagd`` worden in deze bouwomgeving **nooit** gezet (alleen na echte acceptatie op de NAS).
- ``validatie``: één of meer waarden uit de vaste lijst, gescheiden door ``"; "``; nooit leeg; toelichting hoort in
  ``opmerking``. ``Niet getest`` alleen bij ``Niet gestart``; ``Sim-geslaagd``/``Sim-mislukt`` alleen bij Testmatrix-ID's.
- Elke ``NIET GEVALIDEERD (…)`` in ``blokkade`` staat ook in ``validatie``.
- ID's: Excel-ID's uit ``EXCEL_TAKEN.json``, contractpunten ``C1.3-MH-<nn>``/``C1.3-ALG-<nn>`` die in
  ``CONTRACT_1.3_CONFORMITEIT.md`` bestaan, en reviews ``REVIEW-my_house-<onderwerp>``. Elk punt van hoofdstuk 8 van de
  conformiteitslijst heeft een regel (ook de latere fasen, als open).

De regels staan in één functie (``fouten``), zodat de negatieve gevallen hieronder dezelfde code toetsen als de echte
EVIDENCE.json.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

PV2 = Path(__file__).resolve().parents[4] / "docs" / "platform-v2"
VELDEN = {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}
WERKPAKKET = {"Niet gestart", "Bezig", "Gereed", "Geblokkeerd", "N.v.t."}
TESTMATRIX = {"Niet gestart", "Bezig", "Geslaagd", "Mislukt", "Geblokkeerd", "N.v.t."}
NOOIT = {"Gereed", "Geslaagd"}
VALIDATIE = {
    "Sim-geslaagd",
    "Sim-mislukt",
    "Sim-getest",
    "Unit-getest",
    "Intel-iGPU-getest (ontwikkelmachine)",
    "NIET GEVALIDEERD (echte NAS)",
    "NIET GEVALIDEERD (hardware)",
    "NIET GEVALIDEERD (account)",
    "NIET GEVALIDEERD (Mac/Xcode)",
    "Niet getest",
}
TESTMATRIX_ID = re.compile(r"EX\d+|JLT-\d+")
CONTRACTPUNT = re.compile(r"C1\.3-(?:MH|ALG)-\d\d")
REVIEW = re.compile(r"REVIEW-my_house-[a-z0-9][a-z0-9.-]*")
NIET_GEVALIDEERD = re.compile(r"NIET GEVALIDEERD \([^)]*\)")


def _lees(naam: str) -> Any:
    return json.loads((PV2 / naam).read_text(encoding="utf-8"))


def excel_ids() -> set[str]:
    return {t["ID"] for t in _lees("EXCEL_TAKEN.json")}


def conformiteitspunten() -> set[str]:
    tekst = (PV2 / "CONTRACT_1.3_CONFORMITEIT.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| (C1\.3-(?:MH|ALG)-\d\d) \|", tekst, re.M))


def fouten(items: object, excel: set[str], punten: set[str]) -> list[str]:
    """Alle overtredingen van §11 (leeg = in orde)."""
    if not isinstance(items, list) or not items:
        return ["EVIDENCE.json moet een niet-lege lijst zijn"]
    uit: list[str] = []
    gezien: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != VELDEN:
            uit.append(f"{item!r:.60}: precies de velden {sorted(VELDEN)}")
            continue
        rid = item["id"]
        if rid in gezien:
            uit.append(f"{rid}: dubbele id")
        gezien.add(rid)
        if not all(isinstance(item[k], str) for k in VELDEN):
            uit.append(f"{rid}: alle velden zijn tekst")
            continue
        testmatrix = bool(TESTMATRIX_ID.fullmatch(rid))
        if not (rid in excel or testmatrix or (CONTRACTPUNT.fullmatch(rid) and rid in punten) or REVIEW.fullmatch(rid)):
            uit.append(f"{rid}: geen Excel-ID, bestaand contractpunt of REVIEW-my_house-…")
        if item["repo"] != "my_house":
            uit.append(f"{rid}: repo moet my_house zijn")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["bijgewerkt"]):
            uit.append(f"{rid}: bijgewerkt als JJJJ-MM-DD")
        status = item["status"]
        if status in NOOIT:
            uit.append(f"{rid}: '{status}' wordt in deze bouwomgeving nooit gezet")
        elif status not in (TESTMATRIX if testmatrix else WERKPAKKET):
            uit.append(f"{rid}: status '{status}' staat niet in de lijst van het blad")
        if not item["validatie"].strip():
            uit.append(f"{rid}: een lege validatie is nooit toegestaan")
            continue
        waarden = item["validatie"].split("; ")
        onbekend = [w for w in waarden if w not in VALIDATIE]
        if onbekend:
            uit.append(f"{rid}: validatie buiten de vaste lijst: {onbekend} (toelichting hoort in opmerking)")
        if len(waarden) != len(set(waarden)):
            uit.append(f"{rid}: dubbele validatiewaarde")
        if "Niet getest" in waarden and (status != "Niet gestart" or len(waarden) > 1):
            uit.append(f"{rid}: 'Niet getest' alleen (en als enige waarde) bij status 'Niet gestart'")
        if not testmatrix and {"Sim-geslaagd", "Sim-mislukt"} & set(waarden):
            uit.append(f"{rid}: Sim-geslaagd/Sim-mislukt alleen bij Testmatrix-ID's")
        for gevonden in NIET_GEVALIDEERD.findall(item["blokkade"]):
            if gevonden not in waarden:
                uit.append(f"{rid}: '{gevonden}' staat in blokkade maar niet in validatie")
        if status == "Geblokkeerd" and not item["blokkade"].strip():
            uit.append(f"{rid}: Geblokkeerd zonder blokkade")
    for punt in sorted(p for p in punten if p.startswith("C1.3-MH-")):
        if punt not in gezien:
            uit.append(f"{punt}: punt uit CONTRACT_1.3_CONFORMITEIT.md §8 zonder regel (ook latere fasen als open)")
    return uit


@pytest.fixture(scope="module")
def echt() -> list[dict[str, Any]]:
    return _lees("EVIDENCE.json")  # type: ignore[no-any-return]


def test_evidence_volgt_paragraaf_11(echt: list[dict[str, Any]]) -> None:
    assert fouten(echt, excel_ids(), conformiteitspunten()) == []


def test_alle_punten_van_my_house_staan_in_de_lijst() -> None:
    assert {f"C1.3-MH-0{n}" for n in range(1, 8)} <= conformiteitspunten()


def test_latere_fasen_staan_open_en_ongetest(echt: list[dict[str, Any]]) -> None:
    per_id = {e["id"]: e for e in echt}
    for punt in ("C1.3-MH-03", "C1.3-MH-04", "C1.3-MH-05", "C1.3-MH-06"):
        assert per_id[punt]["status"] == "Niet gestart", punt
        assert per_id[punt]["validatie"] == "Niet getest", punt
        assert "P3" in per_id[punt]["opmerking"], punt


def _met(echt: list[dict[str, Any]], **wijzig: str) -> list[dict[str, Any]]:
    kopie = copy.deepcopy(echt)
    kopie[0].update(wijzig)
    return kopie


@pytest.mark.parametrize(
    ("wijziging", "melding"),
    [
        ({"status": "Gereed"}, "nooit gezet"),
        ({"status": "Geslaagd"}, "nooit gezet"),
        ({"status": "Klaar"}, "niet in de lijst"),
        ({"validatie": ""}, "lege validatie"),
        ({"validatie": "Sim-getest; deels uitgevoerd"}, "buiten de vaste lijst"),
        ({"validatie": "Sim-getest; Sim-getest"}, "dubbele validatiewaarde"),
        ({"validatie": "Niet getest", "status": "Bezig"}, "'Niet getest' alleen"),
        ({"validatie": "Sim-geslaagd"}, "alleen bij Testmatrix"),
        ({"validatie": "Sim-getest", "blokkade": "NIET GEVALIDEERD (hardware): echte Gree"}, "staat in blokkade"),
        ({"status": "Geblokkeerd", "blokkade": ""}, "Geblokkeerd zonder blokkade"),
        ({"repo": "ai-server-manager"}, "repo moet my_house"),
        ({"bijgewerkt": "10-11-2026"}, "JJJJ-MM-DD"),
        ({"id": "B99-001"}, "geen Excel-ID"),
        ({"id": "C1.3-MH-99"}, "geen Excel-ID"),
        ({"id": "REVIEW-ai-server-manager-x"}, "geen Excel-ID"),
    ],
)
def test_negatieve_gevallen(echt: list[dict[str, Any]], wijziging: dict[str, str], melding: str) -> None:
    gevonden = fouten(_met(echt, **wijziging), excel_ids(), conformiteitspunten())
    assert any(melding in f for f in gevonden), gevonden


def test_ontbrekend_veld_dubbele_id_en_ontbrekend_punt(echt: list[dict[str, Any]]) -> None:
    kopie = copy.deepcopy(echt)
    del kopie[0]["opmerking"]
    assert any("precies de velden" in f for f in fouten(kopie, excel_ids(), conformiteitspunten()))
    kopie = copy.deepcopy(echt) + [copy.deepcopy(echt[0])]
    assert any("dubbele id" in f for f in fouten(kopie, excel_ids(), conformiteitspunten()))
    kopie = [e for e in copy.deepcopy(echt) if e["id"] != "C1.3-MH-05"]
    assert any("C1.3-MH-05: punt uit" in f for f in fouten(kopie, excel_ids(), conformiteitspunten()))
    assert fouten([], excel_ids(), conformiteitspunten()) == ["EVIDENCE.json moet een niet-lege lijst zijn"]


def test_delta_matrix_noemt_geen_enrollment_meer() -> None:
    """C1.3-MH-07: 'enrollment via HUB_URL' en 'push naar de Hub-inbox na enrollment' alleen nog doorgehaald."""
    tekst = (PV2 / "DELTA_MATRIX.md").read_text(encoding="utf-8")
    zonder_doorhaling = re.sub(r"~~.*?~~", "", tekst, flags=re.S)
    assert "na enrollment" not in zonder_doorhaling
    assert "enrollment via `HUB_URL`" not in zonder_doorhaling.replace("Enrollment via", "enrollment via")
    assert "## 12. Contract 1.3" in tekst
