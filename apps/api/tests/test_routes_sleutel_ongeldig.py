"""KLEIN-43 (vervolg op KLEIN-41/42): SHC_SECRET_KEY_FILE wordt bij elke aanroep opnieuw gelezen, dus een
route kan tijdens het draaien alsnog een ontbrekende of te korte sleutel krijgen. De routes die dan een
kale 500 gaven (PUT /system/backup-settings met wachtwoordzin, GET en PUT /system/notify, POST
/system/notify/test en POST /integrations/{provider}/pair) en de routes met een misleidende melding
(GET /system/notify "andere hoofdsleutel", POST /system/notify/test 409, POST /devices/{id}/test
key_mismatch) weigeren nu met 503 ``secret_key_invalid`` en dezelfde vaste tekst als restore/verify
(KLEIN-41), zonder iets op te slaan en zonder (een deel van) de sleutel. Bij koppelen gebeurt de controle
vóór adapter.pair, zodat er geen sleutel op het apparaat ontstaat die niet bewaard wordt.
"""

import logging
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Integration
from shc_api.store import get_setting

from .ws_devices_support import add_device, add_integration, new_home

TE_KORT = "KortGeheimQ7zX-9wv"  # 18 tekens: SecretBox weigert hem
TEKST = "De sleutel van deze server ontbreekt of is ongeldig; controle niet mogelijk."
URL = "https://ntfy.sh/klein43-onderwerp"


def _geen_sleutelmateriaal(tekst: str) -> None:
    for i in range(len(TE_KORT) - 5):  # ook geen stuk van 6 tekens
        assert TE_KORT[i : i + 6] not in tekst


def _zet_sleutel(monkeypatch, tmp_path, soort: str) -> None:
    """Zoals tijdens het draaien: het sleutelbestand wordt te kort gemaakt of verdwijnt."""
    f = tmp_path / "secret_key"
    if soort == "te_kort":
        f.write_text(TE_KORT + "\n")
    monkeypatch.setattr(get_settings(), "secret_key_file", f)
    monkeypatch.setattr(get_settings(), "secret_key", None)


def _is_weigering(r: httpx.Response) -> None:
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == {"code": "secret_key_invalid", "message": TEKST}


async def _instelling(key: str):
    async with sessionmaker()() as db:
        return await get_setting(db, key)


@pytest.fixture
async def ruw(app, admin):
    """Client die een fout als antwoord teruggeeft (geen exceptie in de test)."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=admin.c.cookies) as c:
        yield c, admin.h()


SOORTEN = pytest.mark.parametrize("soort", ["te_kort", "ontbreekt"])


@SOORTEN
async def test_backup_wachtwoordzin_weigert_en_slaat_niets_op(ruw, monkeypatch, tmp_path, caplog, soort):
    c, h = ruw
    voor = (await _instelling("backup_settings"), await _instelling("backup_passphrase_enc"))
    _zet_sleutel(monkeypatch, tmp_path, soort)
    caplog.set_level(logging.DEBUG)
    r = await c.put(
        "/api/v1/system/backup-settings", json={"passphrase": "nieuwe-wachtwoordzin-1", "keep": 77}, headers=h
    )
    _is_weigering(r)
    assert (await _instelling("backup_settings"), await _instelling("backup_passphrase_enc")) == voor
    _geen_sleutelmateriaal(r.text + caplog.text)


@SOORTEN
async def test_notify_routes_weigeren_met_opgeslagen_adres(ruw, admin, monkeypatch, tmp_path, caplog, soort):
    c, h = ruw
    assert (await admin.put("/system/notify", {"url": URL})).status_code == 200
    opgeslagen = await _instelling("notify_url_enc")
    try:
        _zet_sleutel(monkeypatch, tmp_path, soort)
        caplog.set_level(logging.DEBUG)
        teksten = []
        for r in (
            await c.get("/api/v1/system/notify"),
            await c.put("/api/v1/system/notify", json={"url": "https://ntfy.sh/klein43-ander"}, headers=h),
            await c.post("/api/v1/system/notify/test", headers=h),
        ):
            _is_weigering(r)
            teksten.append(r.text)
        assert await _instelling("notify_url_enc") == opgeslagen  # niets overschreven
        assert "andere hoofdsleutel" not in caplog.text
        _geen_sleutelmateriaal("".join(teksten) + caplog.text)
        # Wissen heeft de sleutel niet nodig en blijft werken.
        r = await c.put("/api/v1/system/notify", json={"url": None}, headers=h)
        assert r.status_code == 200 and r.json()["configured"] is False, r.text
        assert await _instelling("notify_url_enc") is None
    finally:
        monkeypatch.undo()
        assert (await admin.put("/system/notify", {"url": None})).status_code == 200


@SOORTEN
async def test_notify_zonder_opgeslagen_adres_heeft_geen_sleutel_nodig(ruw, monkeypatch, tmp_path, soort):
    """Ongewijzigd: zonder adres in de app is de sleutel niet nodig (lezen, wissen, 409 bij testen)."""
    c, h = ruw
    assert await _instelling("notify_url_enc") is None
    _zet_sleutel(monkeypatch, tmp_path, soort)
    r = await c.get("/api/v1/system/notify")
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["error"] is None, r.text
    assert (await c.put("/api/v1/system/notify", json={"url": None}, headers=h)).status_code == 200
    r = await c.post("/api/v1/system/notify/test", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_configured"


@SOORTEN
async def test_apparaattest_meldt_geen_key_mismatch_maar_weigert(ruw, admin, monkeypatch, tmp_path, caplog, soort):
    from shc_api.routers import devices as devmod

    class Nep:
        def __init__(self, config, credentials, **kw):
            pass

        async def health(self):  # pragma: no cover - mag niet bereikt worden
            raise AssertionError("health zonder bruikbare sleutel")

    monkeypatch.setattr(devmod, "providers", lambda: {"hue": SimpleNamespace(adapter=Nep)})
    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", "Hue", config={}, creds={"application_key": "geheim"})
    lamp = await add_device(home_id, "light", "Plafond", provider="hue", integration_id=integ.id)
    c, h = ruw
    _zet_sleutel(monkeypatch, tmp_path, soort)
    caplog.set_level(logging.DEBUG)
    r = await c.post(f"/api/v1/devices/{lamp.id}/test", headers=h)
    _is_weigering(r)
    assert "key_mismatch" not in r.text
    _geen_sleutelmateriaal(r.text + caplog.text)


@SOORTEN
async def test_koppelen_controleert_sleutel_voor_adapter_pair(ruw, admin, monkeypatch, tmp_path, caplog, soort):
    from shc_api.routers import devices as devmod

    aangeroepen: list[str] = []

    class Nep:
        def __init__(self, config, credentials, **kw):
            self.config = dict(config)

        async def pair(self, host, **kw):
            aangeroepen.append(host)
            raise AssertionError("adapter.pair zonder bruikbare sleutel")

        async def close(self):
            pass

    monkeypatch.setattr(devmod, "providers", lambda: {"hue": SimpleNamespace(adapter=Nep)})
    home_id = await new_home(admin)

    async def _aantal() -> int:
        async with sessionmaker()() as db:
            return (await db.execute(select(func.count()).select_from(Integration))).scalar_one()

    voor = await _aantal()
    c, h = ruw
    _zet_sleutel(monkeypatch, tmp_path, soort)
    caplog.set_level(logging.DEBUG)
    r = await c.post(
        "/api/v1/integrations/hue/pair", json={"home_id": str(home_id), "host": "hue-bridge.local"}, headers=h
    )
    _is_weigering(r)
    assert aangeroepen == []  # geen sleutel op het apparaat aangemaakt
    assert await _aantal() == voor
    _geen_sleutelmateriaal(r.text + caplog.text)
