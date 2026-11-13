"""KLEIN-44 (vervolg op KLEIN-43): de achtergrond (watch.py -> notify.configured_url) logde bij een te korte
sleutel de misleidende WARNING "andere hoofdsleutel". Een ontbrekende of te korte sleutel (de SecretBox kan
niet gemaakt worden) geeft nu een eigen vaste WARNING zonder sleutelmateriaal; een geldige maar andere
sleutel (ontsleutelen mislukt) houdt de oude melding. De uitkomst voor de aanroeper blijft: geen adres uit
de app, dus het adres uit de omgeving of (None, None).
"""

import base64
import logging

import pytest

from shc_api import notify
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.security import SecretBox
from shc_api.store import get_setting, set_setting

TE_KORT = "KortGeheimQ7zX-9wv"  # 18 tekens: SecretBox weigert hem
ANDERE_SLEUTEL = "een-andere-hoofdsleutel-van-een-oude-nas-0123456789"
NIEUWE_MELDING = "Sleutel van deze server ontbreekt of is ongeldig; meldingsadres niet gelezen."
OUDE_MELDING = "andere hoofdsleutel"
URL = "https://ntfy.sh/klein44-onderwerp"


def _blob(sleutel: str, value: str) -> str:
    return base64.b64encode(SecretBox(sleutel).encrypt({"v": value})).decode()


def _geen_sleutelmateriaal(tekst: str) -> None:
    for i in range(len(TE_KORT) - 5):  # ook geen stuk van 6 tekens
        assert TE_KORT[i : i + 6] not in tekst


@pytest.fixture
async def opgeslagen_adres(admin):
    async with sessionmaker()() as db:
        voor = await get_setting(db, notify.SETTING_KEY)
        await set_setting(db, notify.SETTING_KEY, _blob(get_settings().resolved_secret_key(), URL))
        await db.commit()
    yield
    async with sessionmaker()() as db:
        await set_setting(db, notify.SETTING_KEY, voor)
        await db.commit()


def _zet_sleutel(monkeypatch, tmp_path, soort: str) -> None:
    f = tmp_path / "secret_key"
    if soort == "te_kort":
        f.write_text(TE_KORT + "\n")
    monkeypatch.setattr(get_settings(), "secret_key_file", f)
    monkeypatch.setattr(get_settings(), "secret_key", None)


def _waarschuwingen(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == notify.log.name and r.levelno == logging.WARNING]


@pytest.mark.parametrize("omgeving", [None, "https://ntfy.sh/uit-de-omgeving"])
@pytest.mark.parametrize("soort", ["te_kort", "ontbreekt"])
async def test_ontbrekende_of_te_korte_sleutel_eigen_melding(
    opgeslagen_adres, monkeypatch, tmp_path, caplog, soort, omgeving
):
    monkeypatch.setattr(get_settings(), "notify_url", omgeving)
    _zet_sleutel(monkeypatch, tmp_path, soort)
    caplog.set_level(logging.DEBUG)
    async with sessionmaker()() as db:
        uitkomst = await notify.configured_url(db)
    assert uitkomst == ((omgeving, "env") if omgeving else (None, None))
    assert _waarschuwingen(caplog) == [NIEUWE_MELDING]
    assert OUDE_MELDING not in caplog.text
    _geen_sleutelmateriaal(caplog.text)


async def test_geldige_maar_andere_sleutel_houdt_oude_melding(opgeslagen_adres, monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "notify_url", None)
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    monkeypatch.setattr(get_settings(), "secret_key", ANDERE_SLEUTEL)
    caplog.set_level(logging.DEBUG)
    async with sessionmaker()() as db:
        assert await notify.configured_url(db) == (None, None)
    meldingen = _waarschuwingen(caplog)
    assert len(meldingen) == 1 and OUDE_MELDING in meldingen[0]
    assert NIEUWE_MELDING not in caplog.text


# KLEIN-46: notify.stored_url_error (enige aanroeper: routers/system.notify_view, veld "error" van
# GET/PUT /system/notify, str of None) maakt hetzelfde onderscheid als configured_url. Ontbrekende of te
# korte sleutel: eigen vaste tekst; geldige maar andere sleutel: KEY_ERROR. Soort uitkomst blijft str.
SLEUTEL_FOUT = (
    "De sleutel van deze server ontbreekt of is ongeldig; het push-adres dat in de app is ingesteld, kan niet "
    "gelezen worden en wordt niet gebruikt."
)


@pytest.mark.parametrize("soort", ["te_kort", "ontbreekt"])
async def test_stored_url_error_ontbrekende_of_te_korte_sleutel(opgeslagen_adres, monkeypatch, tmp_path, soort):
    _zet_sleutel(monkeypatch, tmp_path, soort)
    async with sessionmaker()() as db:
        fout = await notify.stored_url_error(db)
    assert isinstance(fout, str)
    assert fout == SLEUTEL_FOUT == notify.SLEUTEL_ONBRUIKBAAR_FOUT
    assert fout != notify.KEY_ERROR
    _geen_sleutelmateriaal(fout)


async def test_stored_url_error_geldige_maar_andere_sleutel_blijft_key_error(opgeslagen_adres, monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    monkeypatch.setattr(get_settings(), "secret_key", ANDERE_SLEUTEL)
    async with sessionmaker()() as db:
        assert await notify.stored_url_error(db) == notify.KEY_ERROR


async def test_stored_url_error_eigen_sleutel_geen_fout(opgeslagen_adres):
    async with sessionmaker()() as db:
        assert await notify.stored_url_error(db) is None
