"""Herstelregressies: alleen tijdelijke data en ASGI, nooit echte apparaten."""

import os
import subprocess
import uuid

import pytest

from shc_api.db import sessionmaker
from shc_api.models import ClimateZone, Device

from .boiler_support import boiler_rig as boiler_rig
from .boiler_support import commands, mapping, mappings, protect
from .conftest import DB_NAME
from .test_api import add_device
from .test_boiler_ketens import _legacy_mapping
from .ws_devices_support import new_home


def database_bytes():
    """Logische dump van alle data én sequenties; geen geheimen in testuitvoer."""
    result = subprocess.run(
        ["pg_dump", "--data-only", "--no-owner", "--no-privileges", f"{os.environ['SHC_TEST_PG']}/{DB_NAME}"],
        check=True, capture_output=True,
    ).stdout
    return b"\n".join(line for line in result.splitlines() if not line.startswith((b"\\restrict", b"\\unrestrict")))


async def patch(admin, path, body):
    return await admin.c.patch("/api/v1" + path, json=body, headers=admin.h())


def denied(response):
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied"


async def test_vervangen_naar_boiler_via_api(admin, home):
    hid = await new_home(admin, "Vervangen naar boiler")
    # Alleen de inventaris is testdata; alle rollen en koppelingen ontstaan via de API.
    boiler = await add_device(str(hid), "energy_socket", "Boilerstekker")
    group = await add_device(str(hid), "light_group", "Lichtgroep")
    button = await add_device(str(hid), "button", "Knop")
    assert (await admin.post(f"/devices/{boiler}/bind", json={"role": "boiler"})).status_code == 200
    assert (await admin.get(f"/devices/{boiler}")).json()["protected"] is True
    assert (await admin.post(f"/devices/{group}/bind", json={"role": "bedroom_climate"})).status_code == 200
    saved = await admin.post(f"/buttons/{button}/mappings", json=mapping({"device_id": str(group), "action": "toggle"}))
    assert saved.status_code == 200, saved.text
    mid = saved.json()["mapping"][0]["id"]
    denied(await patch(admin, f"/button-mappings/{mid}", {"action": {"steps": [{"device_id": str(boiler), "action": "toggle"}]}}))
    before = database_bytes()
    denied(await admin.post(f"/devices/{group}/replace", json={"new_device_id": str(boiler)}))
    assert database_bytes() == before


@pytest.mark.parametrize("link", ["unit", "sensor", "knopstap", "knop"])
async def test_vervangen_naar_beschermd_doel_atomair(admin, boiler_rig, link):
    r = boiler_rig
    kind = {"unit": "ac_unit", "sensor": "zigbee_sensor", "knopstap": "light_group", "knop": "button"}[link]
    old = await add_device(str(r.home), kind, "Oud apparaat")
    new = await add_device(str(r.home), kind, "Beschermd apparaat")
    await protect(r.home, new)  # Oude/importtoestand: ook een airco kan een beschermde rol hebben.
    if link in ("unit", "sensor"):
        async with sessionmaker()() as db:
            z = await db.get(ClimateZone, r.zone)
            setattr(z, f"{link}_device_id", old)
            await db.commit()
    else:
        response = await admin.post(f"/buttons/{old if link == 'knop' else r.button}/mappings",
                                    json=mapping({"device_id": str(r.group if link == 'knop' else old), "action": "toggle"}))
        assert response.status_code == 200, response.text
    before = database_bytes()
    denied(await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)}))
    assert database_bytes() == before
    assert await commands(r.home) == []


async def test_boiler_zelf_vervangen_blijft_toegestaan(admin, boiler_rig):
    r = boiler_rig
    # De gewone boiler is geen klimaatunit; de oude/importzone uit de fixture hoort hier niet bij.
    response = await patch(admin, f"/zones/{r.zone}", {"unit_device_id": None})
    assert response.status_code == 200, response.text
    # Een oude knopstap moet wél mee verhuizen; anders bereikt deze proef de nieuwe controle niet.
    await _legacy_mapping(r, {"device_id": str(r.boiler), "action": "set_relay", "params": {"on": False}})
    replacement = await add_device(str(r.home), "energy_socket", "Nieuwe meetstekker")
    response = await admin.post(f"/devices/{r.boiler}/replace", json={"new_device_id": str(replacement)})
    assert response.status_code == 200, response.text
    assert response.json()["new"]["protected"] is True
    assert response.json()["old"]["lifecycle"] == "replaced"
    assert "boiler" in response.json()["moved"]["roles"]
    assert response.json()["moved"]["mappings"] == 1
    assert (await mappings(admin, r))[0]["action"]["steps"][0]["device_id"] == str(replacement)
    assert await commands(r.home) == []


@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize("case", ["archief", "licht-in-klimaat", "zone-in-licht"])
async def test_knopbeleid_voor_archief_en_modus(admin, boiler_rig, method, case):
    r = boiler_rig
    good = mapping({"device_id": str(r.group), "action": "toggle"})
    saved = await admin.post(f"/buttons/{r.button}/mappings", json=good)
    assert saved.status_code == 200, saved.text
    mid = saved.json()["mapping"][0]["id"]
    if case == "archief":
        async with sessionmaker()() as db:
            (await db.get(Device, r.protected_group)).lifecycle = "archived"
            await db.commit()
        bad = mapping({"device_id": str(r.protected_group), "action": "toggle"})
    elif case == "licht-in-klimaat":
        bad = mapping({"device_id": str(r.boiler), "action": "toggle"}, "climate")
    else:
        bad = mapping({"zone_id": str(r.zone), "mode": "heat"}, "lights")
    before = database_bytes()
    if method == "post":
        response = await admin.post(f"/buttons/{r.button}/mappings", json={**bad, "button": "tweede"})
    elif method == "put":
        response = await admin.put(f"/buttons/{r.button}/mappings", json=[good, {**bad, "button": "tweede"}])
    else:
        response = await patch(admin, f"/button-mappings/{mid}", bad)
    denied(response)
    assert database_bytes() == before
    assert await commands(r.home) == []


@pytest.mark.parametrize("target", ["ontbreekt", "andere-woning", "archief-onbeschermd"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "simulatie"])
async def test_knopdoel_afscherming_blijft(admin, boiler_rig, target, method):
    r = boiler_rig
    if target == "ontbreekt":
        tid = uuid.uuid4()
    elif target == "andere-woning":
        hid = await new_home(admin, "Andere woning")
        tid = await add_device(str(hid), "light_group", "Beschermd elders", role="boiler")
    else:
        tid = r.group
        async with sessionmaker()() as db:
            (await db.get(Device, tid)).lifecycle = "archived"
            await db.commit()
    step = {"device_id": str(tid), "action": "toggle"}
    mid = await _legacy_mapping(r, step)
    before = database_bytes()
    if method == "simulatie":
        response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": "testknop", "event": "short_release"})
        assert response.status_code == 200, response.text
        assert response.json()["mappings"][0]["steps"][0]["result"] == "invalid"
    else:
        body = mapping(step)
        if method == "post":
            response = await admin.post(f"/buttons/{r.button}/mappings", json={**body, "button": "tweede"})
        elif method == "put":
            response = await admin.put(f"/buttons/{r.button}/mappings", json=[body])
        else:
            response = await patch(admin, f"/button-mappings/{mid}", body)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "validation_error"
    assert database_bytes() == before


async def test_simulatie_beschermd_archief_weigert_beleid(admin, boiler_rig):
    r = boiler_rig
    await _legacy_mapping(r, {"device_id": str(r.protected_group), "action": "toggle"})
    async with sessionmaker()() as db:
        (await db.get(Device, r.protected_group)).lifecycle = "archived"
        await db.commit()
    before = database_bytes()
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": "testknop", "event": "short_release"})
    assert response.status_code == 200, response.text
    step = response.json()["mappings"][0]["steps"][0]
    assert step["result"] == "refused"
    assert step["code"] == "policy_denied"
    assert response.json()["would_run"] is False
    assert database_bytes() == before


async def test_oude_beschermde_koppeling_alleen_uitzetten(admin, boiler_rig):
    r = boiler_rig
    mid = await _legacy_mapping(r, {"device_id": str(r.protected_group), "action": "toggle"})
    before = (await mappings(admin, r))[0]
    response = await patch(admin, f"/button-mappings/{mid}", {"enabled": False})
    assert response.status_code == 200, response.text
    after = (await mappings(admin, r))[0]
    assert after == {**before, "enabled": False}
    for body in ({"enabled": True}, {"event": "long_press"}, {"enabled": False, "event": "long_press"}):
        snapshot = database_bytes()
        denied(await patch(admin, f"/button-mappings/{mid}", body))
        assert database_bytes() == snapshot
    assert await commands(r.home) == []


async def test_gewone_koppeling_uitzetten_en_aanzetten(admin, boiler_rig):
    r = boiler_rig
    response = await admin.post(f"/buttons/{r.button}/mappings", json=mapping({"device_id": str(r.group), "action": "toggle"}))
    assert response.status_code == 200, response.text
    mid = response.json()["mapping"][0]["id"]
    for enabled in (False, True):
        response = await patch(admin, f"/button-mappings/{mid}", {"enabled": enabled})
        assert response.status_code == 200, response.text
        assert response.json()["mapping"][0]["enabled"] is enabled
