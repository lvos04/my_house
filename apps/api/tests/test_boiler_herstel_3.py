"""Derde herstelronde: scène-eigenaar en oude UUID-notaties, uitsluitend lokale testdata."""

from datetime import UTC, datetime, timedelta

import pytest

from shc_api.db import sessionmaker
from shc_api.models import Device, DeviceBinding, LogicalRole
from shc_api.worker import Worker

from .boiler_support import boiler_rig as boiler_rig
from .boiler_support import commands, mapping, mappings, protect
from .test_api import add_device
from .test_boiler_herstel import database_bytes, denied, patch
from .test_boiler_ketens import Recorder, _legacy_mapping, _press


async def scene_in_group(r, group):
    async with sessionmaker()() as db:
        scene = await db.get(Device, r.scene)
        scene.state = {**scene.state, "group_device_id": str(group)}
        await db.commit()
    return {"device_id": str(r.scene), "action": "recall_scene"}


@pytest.mark.parametrize("method", ["post", "put", "patch"])
async def test_scene_beschermde_groep_opslaan_atomair(admin, boiler_rig, method):
    r = boiler_rig
    good = mapping({"device_id": str(r.group), "action": "toggle"})
    saved = await admin.post(f"/buttons/{r.button}/mappings", json=good)
    assert saved.status_code == 200, saved.text
    mid = saved.json()["mapping"][0]["id"]
    bad = mapping(await scene_in_group(r, r.protected_group), button="tweede")
    before = database_bytes()
    if method == "post":
        response = await admin.post(f"/buttons/{r.button}/mappings", json=bad)
    elif method == "put":
        response = await admin.put(f"/buttons/{r.button}/mappings", json=[good, bad])
    else:
        response = await patch(admin, f"/button-mappings/{mid}", bad)
    assert database_bytes() == before, "Geweigerde scènestap veranderde de database."
    denied(response)
    assert await commands(r.home) == []


async def test_scene_beschermde_groep_simulatie(admin, boiler_rig):
    r = boiler_rig
    await _legacy_mapping(r, await scene_in_group(r, r.protected_group))
    before = database_bytes()
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": "testknop", "event": "short_release"})
    assert database_bytes() == before
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["would_run"] is False
    assert result["mappings"][0]["steps"][0]["result"] == "refused"
    assert result["mappings"][0]["steps"][0]["code"] == "policy_denied"
    assert await commands(r.home) == []


async def test_scene_beschermde_groep_worker(admin, boiler_rig):
    r = boiler_rig
    await _legacy_mapping(r, await scene_in_group(r, r.protected_group))
    w, adapter = Worker(), Recorder()
    w.started_at = datetime.now(UTC) - timedelta(minutes=1)
    w._adapter_for = lambda d: adapter
    await _press(r, w)
    queued = await commands(r.home)
    await w.process_commands({r.home})
    assert adapter.calls == [], "Beschermde scènegroep bereikte de adapter."
    assert queued == [], "Beschermde scènegroep kreeg een commando."
    assert await commands(r.home) == []


@pytest.mark.parametrize("weg", ["binden", "bestaand-doel", "meeverhuizen"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_scene_groep_verwijzing_atomair(admin, boiler_rig, weg, enabled):
    r = boiler_rig
    old = await add_device(str(r.home), "light_group", "Oude groep")
    new = await add_device(str(r.home), "light_group", "Nieuwe groep")
    linked = old if weg == "meeverhuizen" else new
    async with sessionmaker()() as db:
        role = LogicalRole(home_id=r.home, key="boiler_energy", label="Oude meetrol", protected=False)
        db.add(role)
        await db.commit()
        role_id = role.id
    await _legacy_mapping(r, await scene_in_group(r, linked))
    mid = (await mappings(admin, r))[0]["id"]
    if not enabled:
        assert (await patch(admin, f"/button-mappings/{mid}", {"enabled": False})).status_code == 200
    if weg != "binden":
        async with sessionmaker()() as db:
            db.add(DeviceBinding(role_id=role_id, device_id=old))
            await db.commit()
    before = database_bytes()
    if weg == "binden":
        response = await admin.post(f"/devices/{new}/bind", json={"role": "boiler_energy"})
    else:
        response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert database_bytes() == before, "Scèneverwijzing veranderde de database bij beschermde roltoekenning."
    denied(response)
    assert await commands(r.home) == []


@pytest.mark.parametrize("body", [
    {"enabled": True}, {"mode": "climate"}, {"enabled": False, "mode": "climate"}, "ander-doel",
])
async def test_oude_scene_groep_alleen_uitzetten(admin, boiler_rig, body):
    r = boiler_rig
    mid = await _legacy_mapping(r, await scene_in_group(r, r.protected_group))
    if body == "ander-doel":
        body = {"action": {"steps": [{"device_id": str(r.group), "action": "toggle"}]}}
    before = database_bytes()
    response = await patch(admin, f"/button-mappings/{mid}", body)
    assert database_bytes() == before
    denied(response)
    response = await patch(admin, f"/button-mappings/{mid}", {"enabled": False})
    assert response.status_code == 200, response.text
    saved = (await mappings(admin, r))[0]
    assert saved["enabled"] is False and saved["action"]["steps"][0]["device_id"] == str(r.scene)
    async with sessionmaker()() as db:
        assert (await db.get(Device, r.scene)).state["group_device_id"] == str(r.protected_group)


@pytest.mark.parametrize("notatie", ["hoofdletters", "zonder-streepjes"])
@pytest.mark.parametrize("weg", ["binden", "bestaand-doel", "meeverhuizen"])
async def test_oude_uuid_verwijzing_atomair(admin, boiler_rig, notatie, weg):
    r = boiler_rig
    old = await add_device(str(r.home), "energy_socket", "Oude meetstekker")
    new = await add_device(str(r.home), "energy_socket", "Nieuwe meetstekker")
    linked = old if weg == "meeverhuizen" else new
    tekst = str(linked).upper() if notatie == "hoofdletters" else linked.hex
    await _legacy_mapping(r, {"device_id": tekst, "action": "set_light", "params": {"on": False}})
    if weg != "binden":
        await protect(r.home, old)
    before = database_bytes()
    if weg == "binden":
        response = await admin.post(f"/devices/{new}/bind", json={"role": "boiler"})
    else:
        response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert database_bytes() == before, "Oude UUID-verwijzing werd niet herkend."
    denied(response)


async def test_onbeschermde_scene_blijft_werken(admin, boiler_rig):
    r = boiler_rig
    step = await scene_in_group(r, r.group)
    saved = await admin.post(f"/buttons/{r.button}/mappings", json=mapping(step))
    assert saved.status_code == 200, saved.text
    before = database_bytes()
    simulated = await admin.post(f"/buttons/{r.button}/simulate", json={"button": "testknop", "event": "short_release"})
    assert simulated.status_code == 200 and simulated.json()["would_run"] is True
    assert database_bytes() == before
    w, adapter = Worker(), Recorder()
    w.started_at = datetime.now(UTC) - timedelta(minutes=1)
    w._adapter_for = lambda d: adapter
    await _press(r, w)
    assert len(await commands(r.home)) == 1
    await w.process_commands({r.home})
    assert len(adapter.calls) == 1 and adapter.calls[0].action == "recall_scene"


async def test_scene_groep_worker_hercontrole_na_rolwijziging(admin, boiler_rig):
    r = boiler_rig
    await _legacy_mapping(r, await scene_in_group(r, r.group))
    w, adapter = Worker(), Recorder()
    w.started_at = datetime.now(UTC) - timedelta(minutes=1)
    w._adapter_for = lambda d: adapter
    await _press(r, w)
    queued = await commands(r.home)
    assert len(queued) == 1 and queued[0].status == "queued"
    await protect(r.home, r.group)
    await w.process_commands({r.home})
    assert adapter.calls == [], "Worker hercontrole liet een inmiddels beschermde scènegroep door."
    done = await commands(r.home)
    assert len(done) == 1 and done[0].status == "denied"
