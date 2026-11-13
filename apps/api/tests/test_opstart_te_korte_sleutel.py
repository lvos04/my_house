"""KLEIN-42 (vervolg op KLEIN-40/41): bij het opstarten (lifespan in main.py) werd een ontbrekende
SHC_SECRET_KEY al geweigerd met een RuntimeError, maar een te korte sleutel (die SecretBox weigert) niet:
de API startte dan gewoon en liep pas per route vast (kale 500 bij o.a. PUT /system/backup-settings,
PUT /system/notify en POST /integrations/{provider}/pair). Nu weigert het opstarten een te korte sleutel
op dezelfde manier: een RuntimeError met een vaste Nederlandse tekst zonder (een deel van) de sleutel,
nog voordat de achtergrondtaken starten.
"""

import pytest

from shc_api import main
from shc_api.config import get_settings

TE_KORT = "KortGeheimQ7zX-9wv"  # 18 tekens: SecretBox weigert hem


def _geen_sleutelmateriaal(tekst: str) -> None:
    for i in range(len(TE_KORT) - 5):  # ook geen stuk van 6 tekens
        assert TE_KORT[i : i + 6] not in tekst


@pytest.fixture
def gestart(monkeypatch):
    """Registreert of de achtergrondtaken (hub, watch) gestart zouden worden, zonder ze echt te starten."""
    calls: list[str] = []

    async def _hub_start() -> None:
        calls.append("hub")

    monkeypatch.setattr(main.hub, "start", _hub_start)
    monkeypatch.setattr(main.watch, "start", lambda: calls.append("watch"))
    return calls


@pytest.mark.parametrize("via_bestand", [False, True], ids=["env", "bestand"])
async def test_opstarten_weigert_te_korte_sleutel(app, monkeypatch, tmp_path, caplog, gestart, via_bestand):
    if via_bestand:
        f = tmp_path / "secret_key"
        f.write_text(TE_KORT + "\n")
        monkeypatch.setattr(get_settings(), "secret_key_file", f)
        monkeypatch.setattr(get_settings(), "secret_key", None)
    else:
        monkeypatch.setattr(get_settings(), "secret_key_file", None)
        monkeypatch.setattr(get_settings(), "secret_key", TE_KORT)
    with pytest.raises(RuntimeError) as exc:
        async with main.lifespan(app):
            pytest.fail("de API mag met een te korte sleutel niet starten")
    tekst = str(exc.value)
    assert tekst.startswith("SHC_SECRET_KEY of SHC_SECRET_KEY_FILE is te kort (minimaal 32 tekens).")
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert gestart == []
    _geen_sleutelmateriaal(tekst + caplog.text)


async def test_opstarten_weigert_ontbrekende_sleutel_zoals_voorheen(app, monkeypatch, gestart):
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    monkeypatch.setattr(get_settings(), "secret_key", None)
    with pytest.raises(RuntimeError, match="SHC_SECRET_KEY of SHC_SECRET_KEY_FILE ontbreekt"):
        async with main.lifespan(app):
            pytest.fail("de API mag zonder sleutel niet starten")
    assert gestart == []


async def test_opstarten_met_sleutel_van_32_tekens_gaat_door(app, monkeypatch, gestart):
    monkeypatch.setattr(main.watch, "stop", _niets)
    monkeypatch.setattr(main.hub, "stop", _niets)
    monkeypatch.setattr(main, "dispose", _niets)
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    monkeypatch.setattr(get_settings(), "secret_key", "a" * 32)
    async with main.lifespan(app):
        assert gestart == ["hub", "watch"]


async def _niets() -> None:
    return None
