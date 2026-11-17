"""Tweede herstelronde, gebaseerd op H1a–H1d uit de veiligheidsherreview.

Alleen tijdelijke inventaris en ASGI. De historische eerste vervanging omzeilt uitsluitend
de nieuwe soortcontrole, binnen een monkeypatch-context. De daarna getoetste roltoekenning
gebruikt alle productiecontroles: zo bewijzen A en B elkaar niet per ongeluk.
"""

import pytest
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import ClimateZone, Device, LogicalRole
from shc_api.routers import devices

from .boiler_support import boiler_rig as boiler_rig
from .boiler_support import commands, mapping, mappings
from .test_api import add_device
from .test_boiler_herstel import database_bytes, denied, patch
from .test_boiler_ketens import _legacy_mapping
from .ws_devices_support import add_integration, fetch, new_home


async def huis(admin):
    hid = await new_home(admin, "Herstelronde twee")
    ids = {}
    for name, kind in (("boiler", "energy_socket"), ("groep", "light_group"),
                       ("knop", "button"), ("tussen", "energy_socket")):
        ids[name] = await add_device(str(hid), kind, name)
    assert (await admin.post(f"/devices/{ids['boiler']}/bind", json={"role": "boiler"})).status_code == 200
    assert (await admin.post(f"/devices/{ids['groep']}/bind", json={"role": "bedroom_climate"})).status_code == 200
    response = await admin.post(f"/buttons/{ids['knop']}/mappings",
                                json=mapping({"device_id": str(ids['groep']), "action": "toggle"}))
    assert response.status_code == 200, response.text
    return hid, ids


async def historische_vervanging(admin, monkeypatch, old, new):
    with monkeypatch.context() as m:
        m.setattr(devices, "_check_step_kinds", lambda *args: None, raising=False)
        response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("weg", ["h1a", "h1b", "h1c"])
async def test_roltoekenning_na_historische_vervanging(admin, home, monkeypatch, weg):
    hid, ids = await huis(admin)
    target = ids["tussen"]
    if weg == "h1c":
        target = ids["boiler"]
        assert (await admin.delete(f"/devices/{target}")).status_code == 200
        restored = await patch(admin, f"/devices/{target}", {"lifecycle": "active"})
        assert restored.status_code == 200 and restored.json()["protected"] is False
    await historische_vervanging(admin, monkeypatch, ids["groep"], target)
    before = database_bytes()
    if weg == "h1b":
        response = await admin.post(f"/devices/{ids['boiler']}/replace", json={"new_device_id": str(target)})
    else:
        response = await admin.post(f"/devices/{target}/bind", json={"role": "boiler"})
    same = database_bytes() == before
    assert same, "De geweigerde roltoekenning veranderde de database."
    denied(response)
    assert (await admin.get(f"/devices/{target}")).json()["protected"] is False
    assert await commands(hid) == []


@pytest.mark.parametrize("link", ["unit", "sensor", "knopstap", "knop"])
@pytest.mark.parametrize("weg", ["binden", "bestaand-doel", "meeverhuizen"])
@pytest.mark.parametrize("rol", ["boiler_energy", "eigen_beveiligde_rol"])
async def test_h1d_verwijzingen_bij_beschermde_rol(admin, boiler_rig, link, weg, rol):
    r = boiler_rig
    kind = {"unit": "ac_unit", "sensor": "zigbee_sensor", "knopstap": "light_group", "knop": "button"}[link]
    old = await add_device(str(r.home), kind, "Oud doel")
    new = await add_device(str(r.home), kind, "Nieuw doel")
    linked = old if weg == "meeverhuizen" else new
    async with sessionmaker()() as db:
        # boiler_energy moet ook zonder protected-vlag door het standaardbeleid worden beschermd.
        db.add(LogicalRole(home_id=r.home, key=rol, label="Oude meetrol", protected=rol != "boiler_energy"))
        zone = await db.get(ClimateZone, r.zone)
        zone.unit_device_id = linked if link == "unit" else None
        zone.sensor_device_id = linked if link == "sensor" else None
        await db.commit()
    if link in ("knopstap", "knop"):
        response = await admin.post(f"/buttons/{linked if link == 'knop' else r.button}/mappings",
                                    json=mapping({"device_id": str(r.group if link == 'knop' else linked),
                                                  "action": "toggle"}))
        assert response.status_code == 200, response.text
    if weg != "binden":
        # Bij meeverhuizen bestaat de oude/importverwijzing al vóór roltoekenning.
        if weg == "meeverhuizen":
            from shc_api.models import DeviceBinding
            async with sessionmaker()() as db:
                role = (await db.execute(select(LogicalRole).where(
                    LogicalRole.home_id == r.home, LogicalRole.key == rol))).scalar_one()
                db.add(DeviceBinding(role_id=role.id, device_id=old))
                await db.commit()
        else:
            assert (await admin.post(f"/devices/{old}/bind", json={"role": rol})).status_code == 200
    before = database_bytes()
    if weg == "binden":
        response = await admin.post(f"/devices/{new}/bind", json={"role": rol})
    else:
        response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert database_bytes() == before, "De geweigerde verwijzingswijziging veranderde de database."
    denied(response)


@pytest.mark.parametrize("new_kind,action", [
    ("energy_socket", "toggle"), ("scene", "toggle"), ("light", "recall_scene"),
    ("light", "set_group"), ("light_group", "set_light"),
])
async def test_knopstap_vervangen_vereist_passende_soort(admin, home, new_kind, action):
    hid, ids = await huis(admin)
    old_kind = {"recall_scene": "scene", "set_light": "light"}.get(action, "light_group")
    old = await add_device(str(hid), old_kind, "Oude lamp")
    new = await add_device(str(hid), new_kind, "Ongeschikt doel")
    assert (await admin.post(f"/devices/{old}/bind", json={"role": "bedroom_climate"})).status_code == 200
    step = {"device_id": str(old), "action": action, "params": {"on": True}}
    response = await admin.post(f"/buttons/{ids['knop']}/mappings", json=mapping(step, button="tweede"))
    assert response.status_code == 200, response.text
    before = database_bytes()
    response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert database_bytes() == before
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "role_mismatch"


@pytest.mark.parametrize("link", ["unit", "sensor"])
async def test_klimaatvervanging_vereist_passende_soort(admin, boiler_rig, link):
    r = boiler_rig
    kind = "ac_unit" if link == "unit" else "zigbee_sensor"
    old = await add_device(str(r.home), kind, "Oude klimaatkoppeling")
    new = await add_device(str(r.home), "energy_socket", "Ongeschikte meetstekker")
    async with sessionmaker()() as db:
        zone = await db.get(ClimateZone, r.zone)
        zone.unit_device_id = old if link == "unit" else r.unit
        zone.sensor_device_id = old if link == "sensor" else None
        await db.commit()
    before = database_bytes()
    response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert database_bytes() == before
    assert response.status_code == 409 and response.json()["detail"]["code"] == "role_mismatch"


@pytest.mark.parametrize("target", ["licht", "zone"])
async def test_simulatie_verkeerde_modus_eerst_beleid(admin, boiler_rig, target):
    r = boiler_rig
    step = {"device_id": str(r.boiler), "action": "toggle"} if target == "licht" else {"zone_id": str(r.zone), "mode": "heat"}
    await _legacy_mapping(r, step, "climate" if target == "licht" else "lights")
    before = database_bytes()
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": "testknop", "event": "short_release"})
    assert database_bytes() == before
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["would_run"] is False
    assert result["mappings"][0]["steps"][0]["result"] == "refused"
    assert result["mappings"][0]["steps"][0]["code"] == "policy_denied"
    assert await commands(r.home) == []


async def test_lamp_door_lamp_vervangen_blijft_werken(admin, boiler_rig):
    r = boiler_rig
    old = await add_device(str(r.home), "light", "Oude lamp")
    new = await add_device(str(r.home), "light", "Nieuwe lamp")
    saved = await admin.post(f"/buttons/{r.button}/mappings",
                             json=mapping({"device_id": str(old), "action": "toggle"}))
    assert saved.status_code == 200, saved.text
    before = database_bytes()
    response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert response.status_code == 200, response.text
    assert database_bytes() != before
    assert (await mappings(admin, r))[0]["action"]["steps"][0]["device_id"] == str(new)


async def test_archief_terugzetten_herstelt_geen_rol(admin, boiler_rig):
    r = boiler_rig
    assert (await admin.delete(f"/devices/{r.boiler}")).status_code == 200
    response = await patch(admin, f"/devices/{r.boiler}", {"lifecycle": "active"})
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == [] and response.json()["protected"] is False


@pytest.mark.parametrize("weg", ["pair", "ingest"])
async def test_pair_en_ingest_behouden_lichtdoel_en_rollen(admin, boiler_rig, monkeypatch, weg):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from shc_integrations.base import DeviceInfo, DiscoveredDevice, PairResult

    from shc_api.worker import Worker

    r = boiler_rig
    integ = await add_integration(r.home, "homewizard")
    async with sessionmaker()() as db:
        d = await db.get(Device, r.group)
        d.provider, d.integration_id = "homewizard", integ.id
        identity = d.stable_identity
        await db.commit()
    await _legacy_mapping(r, {"device_id": str(r.group), "action": "toggle"})
    before = (await admin.get(f"/devices/{r.group}")).json()
    if weg == "pair":
        class PairAdapter:
            def __init__(self, config, credentials):
                self.config = config

            async def pair(self, host, **kw):
                found = DiscoveredDevice("homewizard", identity, "energy_socket", "Testdoel", host)
                return PairResult(identity, {}, found)

            async def close(self):
                pass

        monkeypatch.setattr(devices, "providers", lambda: {"homewizard": SimpleNamespace(adapter=PairAdapter)})
        response = await admin.post("/integrations/homewizard/pair",
                                    json={"home_id": str(r.home), "host": "boilertest.local"})
        assert response.status_code == 200, response.text
    else:
        worker = Worker()
        await worker.handle_events(integ, [DeviceInfo(identity, "energy_socket", "Testdoel", datetime.now(UTC))])
    assert (await fetch(Device, r.group)).kind == "light_group"
    after = (await admin.get(f"/devices/{r.group}")).json()
    assert after["roles"] == before["roles"] and after["protected"] == before["protected"]
    assert (await mappings(admin, r))[0]["action"]["steps"][0]["device_id"] == str(r.group)
    assert await commands(r.home) == []


@pytest.mark.parametrize("current", ["light", "light_group", "scene", "ac_unit", "zigbee_sensor"])
def test_geldige_doelsoorten_worden_niet_opgewaardeerd(current):
    from shc_integrations.base import should_upgrade_kind

    for new in ("energy_socket", "button", "ac_unit", "zigbee_sensor", "light", "light_group", "scene"):
        assert should_upgrade_kind(current, new) is False
    assert should_upgrade_kind("zigbee_device", "zigbee_sensor") is True
