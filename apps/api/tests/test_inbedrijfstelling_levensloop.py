"""Regressies voor beheertransacties en de lokale Zigbee-beheerketen."""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from shc_integrations.base import ButtonEvent, DeviceInfo, DiscoveredDevice, PairResult, Reading, StateUpdate
from shc_integrations.zigbee import ZigbeeAdapter
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import ButtonMapping, ClimateZone, Device, LogicalRole
from shc_api.routers import devices, lights
from shc_api.store import set_setting
from shc_api.worker import Worker

from .boiler_support import boiler_rig as boiler_rig
from .boiler_support import commands, mapping, protect
from .test_api import add_device
from .test_boiler_ketens import Recorder, _legacy_mapping, _press
from .test_inbedrijfstelling_groepen import change
from .test_inbedrijfstelling_groepen import group_rig as group_rig
from .test_inbedrijfstelling_handhaving import commissioning_state as commissioning_state
from .test_inbedrijfstelling_handhaving import snapshot
from .ws_devices_support import add_integration, new_home


@pytest.mark.parametrize("first", ["bind", "mapping"])
async def test_v2_binden_en_knopkoppeling_zijn_een_serialiseerbaar_paar(admin, group_rig, monkeypatch, first):
    r = group_rig
    target = r.members[0]
    # Een oude rol zonder soortbeperking, zoals in de oorspronkelijke twee-verzoekenproef.
    async with sessionmaker()() as db:
        db.add(LogicalRole(home_id=r.home, key="boiler_energy", label="Oude meetrol", protected=True))
        await db.commit()
    module = devices if first == "bind" else lights
    original_audit = module.audit
    ready, release = asyncio.Event(), asyncio.Event()

    async def pause_before_commit(db, actor, action, *args, **kwargs):
        if action == ("role_bound" if first == "bind" else "button_mapping_added"):
            ready.set()
            await release.wait()
        return await original_audit(db, actor, action, *args, **kwargs)

    monkeypatch.setattr(module, "audit", pause_before_commit)

    async def request(operation):
        if operation == "bind":
            return await admin.post(f"/devices/{target}/bind", json={"role": "boiler_energy"})
        return await admin.post(f"/buttons/{r.button}/mappings", json={
            "button": r.inv.B3, "event": "short_release", "mode": "lights",
            "action": {"device_id": str(target), "action": "toggle"},
        })

    earlier = asyncio.create_task(request(first))
    later = None
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        later = asyncio.create_task(request("mapping" if first == "bind" else "bind"))
        done, _ = await asyncio.wait({later}, timeout=0.15)
        assert not done, "Het tweede verzoek passeerde de controle vóór de commit van het eerste."
        release.set()
        accepted, denied = await asyncio.wait_for(asyncio.gather(earlier, later), timeout=10)
    finally:
        release.set()
        await asyncio.gather(*(task for task in (earlier, later) if task is not None), return_exceptions=True)
    assert accepted.status_code == 200, accepted.text
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "policy_denied", denied.text
    d = (await admin.get(f"/devices/{target}")).json()
    m = (await admin.get(f"/buttons/{r.button}")).json()
    assert d["compatibility"] == "tested"
    assert bool(d["protected"]) == (first == "bind")
    assert bool(m["mapping"]) == (first == "mapping")
    assert await commands(r.home) == []


@pytest.mark.parametrize("condition", ["untested", "protected", "safe_mode", "archived"])
async def test_permit_join_uitzondering_tot_de_zigbee_adapter(admin, app, condition):
    hid = await new_home(admin, "Zigbeeproef")
    setup = await admin.post("/integrations/zigbee/setup", json={"home_id": str(hid)})
    assert setup.status_code == 200, setup.text
    bridge = uuid.UUID(setup.json()["id"])
    await change(bridge, online=True)
    if condition == "protected":
        await protect(hid, bridge)
    elif condition == "safe_mode":
        async with sessionmaker()() as db:
            await set_setting(db, "safe_mode", True)
            await db.commit()
    elif condition == "archived":
        await change(bridge, lifecycle="archived")
    before = await snapshot()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=admin.c.base_url, cookies=admin.c.cookies) as client:
        response = await client.post("/api/v1/integrations/zigbee/permit-join",
                                     json={"home_id": str(hid), "seconds": 60}, headers=admin.h())
    code = {"protected": "policy_denied", "safe_mode": "policy_denied", "archived": "not_configured"}.get(condition)
    assert response.status_code == (409 if code else 202), response.text
    if code:
        assert response.json()["detail"]["code"] == code, response.text
        assert await snapshot() == before
    published = []

    async def publish(topic, body):
        published.append((topic, body))

    adapter, worker = ZigbeeAdapter({"host": "mqtt"}, publisher=publish), Worker()
    worker._adapter_for = lambda d: adapter
    await worker.process_commands({hid})
    rows = await commands(hid)
    if code:
        assert rows == [] and published == []
    else:
        assert len(rows) == 1
        assert rows[0].status == "sent" and rows[0].device_id == bridge
        assert published == [("zigbee2mqtt/bridge/request/permit_join", {"time": 60})]
        assert (await admin.get(f"/devices/{bridge}")).json()["compatibility"] == "untested"


@pytest.mark.parametrize("change_after_request", [{"lifecycle": "archived"}, {
    "kind": "light", "compatibility": "tested",
}], ids=["archief", "andere-soort"])
async def test_worker_hercontroleert_koppelen_op_het_actuele_bridge_doel(admin, change_after_request):
    hid = await new_home(admin, "Zigbeewachtrijproef")
    setup = await admin.post("/integrations/zigbee/setup", json={"home_id": str(hid)})
    assert setup.status_code == 200, setup.text
    bridge = uuid.UUID(setup.json()["id"])
    await change(bridge, online=True)
    queued = await admin.post("/integrations/zigbee/permit-join", json={"home_id": str(hid), "seconds": 60})
    assert queued.status_code == 202, queued.text
    await change(bridge, **change_after_request)
    recorder, worker = Recorder(), Worker()
    worker._adapter_for = lambda d: recorder
    await worker.process_commands({hid})
    rows = await commands(hid)
    assert len(rows) == 1 and rows[0].status == "denied", "De worker liet een ongeschikte bridge door."
    assert recorder.calls == []


@pytest.mark.parametrize("notation", ["hex", "upper", "braces", "urn"])
async def test_vervangen_verhuist_alleen_de_uuid_verwijzing_zonder_testbewijs(admin, group_rig, notation):
    r = group_rig
    old, new = r.members
    old_ref = {"hex": old.hex, "upper": str(old).upper(), "braces": "{" + str(old) + "}", "urn": old.urn}[notation]
    await change(new, compatibility="untested", tested_at=None)
    old_step = {"device_id": old_ref, "action": "toggle", "label": str(old)}
    saved = await _legacy_mapping(r, old_step)
    # Laat dezelfde fysieke knop de verhuisde stap activeren.
    async with sessionmaker()() as db:
        (await db.get(ButtonMapping, saved)).button = r.inv.B3
        await db.commit()
    response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert response.status_code == 200, response.text
    assert response.json()["old"]["compatibility"] == "tested"
    assert response.json()["new"]["compatibility"] == "untested"
    assert response.json()["moved"]["mappings"] == 1, "De oude UUID-verwijzing is niet verhuisd."
    async with sessionmaker()() as db:
        row = await db.get(ButtonMapping, saved)
        assert row.action["steps"] == [{**old_step, "device_id": str(new)}]
    first_event = ButtonEvent(r.inv.D_DIMMER, r.inv.B3, "short_release", datetime.now(UTC), event_id=uuid.uuid4().hex)
    await r.worker.handle_events(r.integ, [first_event])
    await r.worker.process_commands({r.home})
    assert await commands(r.home) == [] and r.fake.calls == []
    confirmed = await admin.post(f"/devices/{new}/tested", json={})
    assert confirmed.status_code == 200, confirmed.text
    next_event = ButtonEvent(r.inv.D_DIMMER, r.inv.B3, "short_release", datetime.now(UTC), event_id=uuid.uuid4().hex)
    await r.worker.handle_events(r.integ, [next_event])
    await r.worker.process_commands({r.home})
    rows = await commands(r.home)
    assert len(rows) == len(r.fake.calls) == 1
    assert rows[0].status == "confirmed" and rows[0].device_id == new


async def test_v3_vervangweigering_noemt_de_uitgezette_oude_koppeling(admin, boiler_rig):
    r = boiler_rig
    new = await add_device(str(r.home), "energy_socket", "Nieuwe testmeter")
    mid = await _legacy_mapping(r, {"device_id": r.boiler.hex, "action": "set_relay"})
    async with sessionmaker()() as db:
        (await db.get(ClimateZone, r.zone)).unit_device_id = None
        (await db.get(ButtonMapping, mid)).enabled = False
        await db.commit()
    before = await snapshot()
    denied = await admin.post(f"/devices/{r.boiler}/replace", json={"new_device_id": str(new)})
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["code"] == "policy_denied"
    assert "knopkoppeling" in denied.json()["detail"]["message"].lower(), denied.text
    assert await snapshot() == before and await commands(r.home) == []
    assert (await admin.delete(f"/button-mappings/{mid}")).status_code == 204
    accepted = await admin.post(f"/devices/{r.boiler}/replace", json={"new_device_id": str(new)})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["new"]["protected"] is True


async def test_v4_dode_stap_verhuist_niet_bij_vervangen_door_dezelfde_soort(admin, boiler_rig):
    r = boiler_rig
    old = await add_device(str(r.home), "energy_socket", "Oude testmeter", role="airco")
    new = await add_device(str(r.home), "energy_socket", "Nieuwe testmeter")
    action = {"device_id": str(old), "action": "set_relay", "params": {"on": False}}
    mid = await _legacy_mapping(r, action)
    response = await admin.post(f"/devices/{old}/replace", json={"new_device_id": str(new)})
    assert response.status_code == 200, response.text
    assert response.json()["moved"]["mappings"] == 0
    async with sessionmaker()() as db:
        assert (await db.get(ButtonMapping, mid)).action == mapping(action)["action"]
    recorder, worker = Recorder(), Worker()
    worker._adapter_for = lambda d: recorder
    await _press(r, worker)
    await worker.process_commands({r.home})
    assert await commands(r.home) == [] and recorder.calls == []


@pytest.mark.parametrize("transition", [
    "archive-user", "archive-adapter", "upgrade-info", "upgrade-state", "pair-upgrade", "pair-archive",
])
async def test_nieuwe_soort_of_reactivatie_vraagt_actueel_testbewijs(admin, group_rig, monkeypatch, transition):
    r = group_rig
    now = datetime.now(UTC)
    readback = r.model.snapshot(now)
    if transition.startswith("upgrade") or transition == "pair-upgrade":
        await change(r.group, kind="hue_device")
    marked = await admin.post(f"/devices/{r.group}/tested", json={"note": "Eerder getest"})
    assert marked.status_code == 200, marked.text
    if transition == "archive-user":
        assert (await admin.delete(f"/devices/{r.group}")).status_code == 200
        activated = await admin.c.patch(f"/api/v1/devices/{r.group}", json={"lifecycle": "active"}, headers=admin.h())
        assert activated.status_code == 200, activated.text
    elif transition == "archive-adapter":
        await r.worker.handle_events(r.integ, [DeviceInfo(r.inv.G_LIVING, "light_group", None, now, removed=True)])
    elif transition == "upgrade-state":
        readback = [ev for ev in readback if isinstance(ev, StateUpdate)]
    elif transition.startswith("pair-"):
        if transition == "pair-archive":
            await change(r.group, lifecycle="archived")

        class LocalPair:
            def __init__(self, config, credentials):
                self.config = config

            async def pair(self, host, **options):
                device = DiscoveredDevice("hue", r.inv.G_LIVING, "light_group", "Proefgroep", host)
                return PairResult(r.inv.G_LIVING, {}, device)

        monkeypatch.setattr(devices, "providers", lambda: {"hue": SimpleNamespace(adapter=LocalPair, label="Hue")})
        paired = await admin.post("/integrations/hue/pair", json={"home_id": str(r.home), "host": "proef.local"})
        assert paired.status_code == 200, paired.text
    await r.worker.handle_events(r.integ, readback)
    async with sessionmaker()() as db:
        group = await db.get(Device, r.group)
        assert group.kind == "light_group" and group.lifecycle == "active"
        assert group.compatibility == "untested", "Het vorige testbewijs is nog actief."
        assert group.tested_at is None and "tested" not in group.diagnostics
    denied = await admin.post(f"/lights/groups/{r.group}", json={"on": True})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "device_not_tested", denied.text
    assert await commands(r.home) == [] and r.fake.calls == []
    assert (await admin.post(f"/devices/{r.group}/tested", json={})).status_code == 200
    accepted = await admin.post(f"/lights/groups/{r.group}", json={"on": True})
    assert accepted.status_code == 202, accepted.text
    await r.worker.process_commands({r.home})
    assert (await commands(r.home))[0].status == "confirmed" and len(r.fake.calls) == 1


@pytest.mark.parametrize("entrance", ["reading", "button"])
async def test_passieve_soortopwaardering_neemt_geen_testbewijs_mee(admin, group_rig, entrance):
    r = group_rig
    now = datetime.now(UTC)
    if entrance == "reading":
        integ = await add_integration(r.home, "zigbee")
        identity = uuid.uuid4().hex
        await r.worker.handle_events(integ, [DeviceInfo(identity, "zigbee_device", "Proefsensor", now)])
        async with sessionmaker()() as db:
            device = (await db.execute(select(Device).where(Device.stable_identity == identity))).scalar_one()
        device_id = device.id
        event = Reading(identity, "temperature", 21.0, "°C", now)
        expected = "zigbee_sensor"
    else:
        integ, device_id = r.integ, r.button
        await change(device_id, kind="hue_device")
        event = ButtonEvent(r.inv.D_DIMMER, r.inv.B3, "short_release", now, event_id=uuid.uuid4().hex)
        expected = "button"
    assert (await admin.post(f"/devices/{device_id}/tested", json={})).status_code == 200
    await r.worker.handle_events(integ, [event])
    async with sessionmaker()() as db:
        actual = await db.get(Device, device_id)
        assert actual.kind == expected and actual.compatibility == "untested", "Oud testbewijs bleef actief."
        assert actual.tested_at is None and "tested" not in actual.diagnostics
    assert await commands(r.home) == [] and r.fake.calls == []
    assert (await admin.post(f"/devices/{device_id}/tested", json={})).json()["compatibility"] == "tested"


async def test_binden_geeft_geen_testbewijs_van_het_vorige_apparaat_door(admin, group_rig):
    r = group_rig
    old, new = r.members
    role = {"role": "living_room_climate"}
    assert (await admin.post(f"/devices/{old}/bind", json=role)).status_code == 200
    await change(new, compatibility="untested", tested_at=None)
    bound = await admin.post(f"/devices/{new}/bind", json=role)
    assert bound.status_code == 200, bound.text
    assert bound.json()["compatibility"] == "untested", "De binding heeft testbewijs doorgegeven."
    assert role["role"] in bound.json()["roles"]
    assert (await admin.get(f"/devices/{old}")).json()["compatibility"] == "tested"


@pytest.mark.parametrize("lifecycle", ["archived", "replaced"])
async def test_oude_geteste_scene_is_na_archiveren_of_vervangen_niet_uitvoerbaar(admin, group_rig, lifecycle):
    r = group_rig
    path = f"/lights/groups/{r.group}"
    body = {"scene_id": str(r.scene)}
    queued = await admin.post(path, json=body)
    assert queued.status_code == 202, queued.text
    # Oude/importdata kan een opdracht bevatten die het normale archiveren al had geannuleerd.
    await change(r.scene, lifecycle=lifecycle)
    before = await snapshot()
    denied = await admin.post(path, json=body)
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "policy_denied", denied.text
    assert await snapshot() == before
    await r.worker.process_commands({r.home})
    rows = await commands(r.home)
    assert len(rows) == 1 and rows[0].status == "denied", "De oude scèneopdracht bleef uitvoerbaar."
    assert r.fake.calls == []
