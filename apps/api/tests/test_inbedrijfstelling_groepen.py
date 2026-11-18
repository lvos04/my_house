"""Samengestelde doelen: volledige Hue-keten, zonder echte bridge of apparaten."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from shc_integrations.base import ButtonEvent
from shc_integrations.hue import HueAdapter, HueModel
from sqlalchemy import select

from shc_api.commanding import queue_command
from shc_api.db import sessionmaker
from shc_api.models import ButtonMapping, CommandRecord, Device
from shc_api.policy import PolicyDenied
from shc_api.worker import Worker

from .boiler_support import protect
from .test_inbedrijfstelling_handhaving import commissioning_state as commissioning_state
from .test_inbedrijfstelling_handhaving import set_gate, snapshot
from .test_ws_climate_worker import FakeUnit
from .ws_devices_support import add_integration, hue_inventory, new_home


@pytest.fixture
async def group_rig(admin):
    home_id = await new_home(admin, "Groepsproef")
    integ = await add_integration(home_id, "hue")
    inv, model, worker, fake = hue_inventory(), HueModel(), Worker(), FakeUnit()
    model.load(inv.full_resources())
    await worker.handle_events(integ, model.snapshot(datetime.now(UTC)))
    async with sessionmaker()() as db:
        devs = list((await db.execute(select(Device).where(Device.integration_id == integ.id))).scalars())
        by_identity = {d.stable_identity: d.id for d in devs}
        # Expliciete proefopstelling: de beheerder heeft deze fictieve doelen gecontroleerd.
        for dev in devs:
            if dev.kind in {"light", "light_group", "scene"}:
                dev.compatibility, dev.tested_at = "tested", datetime.now(UTC)
        await db.commit()
    worker._adapter_for = lambda dev: fake
    return SimpleNamespace(home=home_id, integ=integ, inv=inv, model=model, worker=worker, fake=fake,
                           group=by_identity[inv.G_LIVING], scene=by_identity[inv.S_EVENING],
                           members=[by_identity[inv.L1], by_identity[inv.L2]],
                           button=by_identity[inv.D_DIMMER])


async def change(device_id, **values):
    async with sessionmaker()() as db:
        dev = await db.get(Device, device_id)
        for key, value in values.items():
            setattr(dev, key, value)
        await db.commit()


async def commands(r):
    async with sessionmaker()() as db:
        return list((await db.execute(select(CommandRecord).where(CommandRecord.home_id == r.home))).scalars())


async def send(admin, r, scene):
    return await admin.post(f"/lights/groups/{r.group}",
                            json={"scene_id": str(r.scene)} if scene else {"on": True})


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("defect", ["untested", "unsupported", "missing", "archived", "replaced",
                                    "kind", "integration", "unknown", "incomplete", "malformed"])
async def test_groepslid_weigert_geheel_zonder_mutatie(admin, group_rig, scene, defect):
    r = group_rig
    if defect in {"untested", "unsupported", "missing"}:
        await change(r.members[1], compatibility="" if defect == "missing" else defect, tested_at=None)
    elif defect in {"archived", "replaced"}:
        await change(r.members[1], lifecycle=defect)
    elif defect == "kind":
        await change(r.members[1], kind="zigbee_device")
    elif defect == "integration":
        other = await add_integration(r.home, "hue")
        await change(r.members[1], integration_id=other.id)
    else:
        async with sessionmaker()() as db:
            group = await db.get(Device, r.group)
            state = dict(group.state)
            if defect == "unknown":
                state["member_identities"] = [r.inv.L1, str(uuid.uuid4())]
            elif defect == "incomplete":
                state["member_identities"] = None
            else:
                state["member_identities"] = [r.inv.L1, 42]
            group.state = state
            await db.commit()
    before = await snapshot()
    response = await send(admin, r, scene)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "device_not_tested", response.text
    assert "command_id" not in response.json()
    assert await snapshot() == before
    await r.worker.process_commands({r.home})
    assert r.fake.calls == []
    assert await commands(r) == []


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_groep_en_scene_positief_tot_adapter(admin, group_rig, scene, enabled):
    r = group_rig
    await set_gate(enabled)
    if not enabled:
        for did in [r.group, r.scene, *r.members]:
            await change(did, compatibility="untested", tested_at=None)
    response = await send(admin, r, scene)
    assert response.status_code == 202, response.text
    await r.worker.process_commands({r.home})
    assert len(r.fake.calls) == 1
    rows = await commands(r)
    assert len(rows) == 1 and rows[0].status == "confirmed"
    assert rows[0].device_id == (r.scene if scene else r.group)
    async with sessionmaker()() as db:
        assert (await db.get(Device, r.members[1])).compatibility == ("tested" if enabled else "untested")


@pytest.mark.parametrize("protected_target", ["group", "member", "scene"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_indirecte_bescherming_voor_alle_teststatussen(admin, group_rig, protected_target, enabled):
    r = group_rig
    await set_gate(enabled)
    await change(r.group, compatibility="untested", tested_at=None)
    await change(r.scene, compatibility="untested", tested_at=None)
    await change(r.members[0], compatibility="untested", tested_at=None)
    target = {"group": r.group, "member": r.members[1], "scene": r.scene}[protected_target]
    await protect(r.home, target)
    before = await snapshot()
    response = await send(admin, r, True)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied", response.text
    assert "boiler" in response.json()["detail"]["message"]
    assert await snapshot() == before


@pytest.mark.parametrize("scene", [False, True])
async def test_commandokern_toetst_leden_voor_schrijven(admin, group_rig, scene):
    r = group_rig
    await change(r.members[1], compatibility="untested", tested_at=None)
    before = await snapshot()
    async with sessionmaker()() as db:
        target = await db.get(Device, r.scene if scene else r.group)
        with pytest.raises(PolicyDenied) as caught:
            await queue_command(db, r.home, target, "recall_scene" if scene else "set_group", {}, "button", "proef")
        assert caught.value.code == "device_not_tested"
        await db.commit()
    assert await snapshot() == before


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("after_claim", [False, True])
@pytest.mark.parametrize("defect", ["untested", "protected", "membership"])
async def test_worker_herleest_ieder_lid(admin, group_rig, monkeypatch, scene, after_claim, defect):
    r = group_rig
    assert (await send(admin, r, scene)).status_code == 202

    async def revoke():
        if defect == "untested":
            await change(r.members[1], compatibility="untested", tested_at=None)
        elif defect == "protected":
            await protect(r.home, r.members[1])
        else:
            async with sessionmaker()() as db:
                group = await db.get(Device, r.group)
                group.state = {**group.state, "member_identities": [r.inv.L1, str(uuid.uuid4())]}
                await db.commit()

    if after_claim:
        original = r.worker._claim_next

        async def claim(home_ids, **kwargs):
            result = await original(home_ids, **kwargs)
            if result:
                await revoke()
            return result

        monkeypatch.setattr(r.worker, "_claim_next", claim)
    else:
        await revoke()
    await r.worker.process_commands({r.home})
    rows = await commands(r)
    assert len(rows) == 1 and rows[0].status == "denied"
    assert ("boiler" if defect == "protected" else "niet") in rows[0].detail
    assert r.fake.calls == []


def mapping(r, scene):
    return {"button": r.inv.B3, "event": "short_release", "mode": "lights", "action": {"steps": [
        {"device_id": str(r.scene if scene else r.group), "action": "recall_scene" if scene else "toggle"},
    ]}}


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize("allowed", [False, True])
async def test_knopopslag_leden_negatief_en_positief(admin, group_rig, scene, method, allowed):
    r = group_rig
    body = mapping(r, scene)
    if method == "patch":
        saved = await admin.post(f"/buttons/{r.button}/mappings", json=body)
        assert saved.status_code == 200, saved.text
        mid = saved.json()["mapping"][0]["id"]
    if not allowed:
        await change(r.members[1], compatibility="untested", tested_at=None)
    before = await snapshot()
    if method == "post":
        response = await admin.post(f"/buttons/{r.button}/mappings", json=body)
    elif method == "put":
        response = await admin.put(f"/buttons/{r.button}/mappings", json=[body])
    else:
        response = await admin.c.patch(f"/api/v1/button-mappings/{mid}", json=body, headers=admin.h())
    assert response.status_code == (200 if allowed else 409), response.text
    if not allowed:
        assert response.json()["detail"]["code"] == "device_not_tested"
        assert await snapshot() == before
    assert await commands(r) == []
    assert r.fake.calls == []


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
async def test_simulatie_en_fysieke_event_met_replay(admin, group_rig, scene, allowed):
    r = group_rig
    body = mapping(r, scene)
    # Oude/importkoppeling: de proef mag de nieuwe opslagcontrole niet als omweg gebruiken.
    async with sessionmaker()() as db:
        db.add(ButtonMapping(home_id=r.home, device_id=r.button, **body))
        await db.commit()
    if not allowed:
        await change(r.members[1], compatibility="untested", tested_at=None)
    before = await snapshot()
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": r.inv.B3, "event": "short_release"})
    assert response.status_code == 200, response.text
    result = response.json()
    step = result["mappings"][0]["steps"][0]
    assert result["would_run"] is allowed, result
    assert step["result"] == ("would_run" if allowed else "refused"), result
    if not allowed:
        assert step["code"] == "device_not_tested", result
    assert await snapshot() == before
    assert r.fake.calls == []
    event = ButtonEvent(r.inv.D_DIMMER, r.inv.B3, "short_release", datetime.now(UTC), event_id=uuid.uuid4().hex)
    await r.worker.handle_events(r.integ, [event])
    assert len(await commands(r)) == int(allowed)
    await r.worker.handle_events(r.integ, [event])
    assert len(await commands(r)) == int(allowed)
    await r.worker.process_commands({r.home})
    assert len(r.fake.calls) == int(allowed)
    if allowed:
        assert (await commands(r))[0].status == "confirmed"


@pytest.mark.parametrize("notation", ["hex", "upper", "braces", "urn"])
async def test_scene_groepsverwijzing_zoals_worker_uuid_leest(admin, group_rig, notation):
    r = group_rig
    value = {"hex": r.group.hex, "upper": str(r.group).upper(), "braces": "{" + str(r.group) + "}",
             "urn": r.group.urn}[notation]
    await change(r.scene, state={"group_device_id": value})
    assert (await send(admin, r, True)).status_code == 202
    await change(r.members[1], compatibility="untested", tested_at=None)
    before = await snapshot()
    response = await send(admin, r, True)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "device_not_tested"
    assert await snapshot() == before


@pytest.mark.parametrize("defect", ["missing", "invalid", "wrong_home", "wrong_kind", "wrong_integration"])
async def test_scene_met_onbekende_groep_blijft_dicht(admin, group_rig, defect):
    r = group_rig
    if defect == "wrong_home":
        await change(r.scene, home_id=await new_home(admin, "Andere woning"))
    elif defect == "wrong_kind":
        await change(r.group, kind="light")
    elif defect == "wrong_integration":
        other = await add_integration(r.home, "hue")
        await change(r.scene, integration_id=other.id)
    else:
        await change(r.scene, state={"group_device_id": None if defect == "missing" else "ongeldig"})
    before = await snapshot()
    async with sessionmaker()() as db:
        target = await db.get(Device, r.scene)
        with pytest.raises(PolicyDenied) as caught:
            await queue_command(db, target.home_id, target, "recall_scene", {}, "user", "proef")
        assert caught.value.code == "device_not_tested"
        await db.commit()
    assert await snapshot() == before


async def test_groepscontrole_voor_semantische_validatie(admin, group_rig):
    r = group_rig
    await change(r.members[1], compatibility="untested", tested_at=None)
    before = await snapshot()
    response = await admin.post(f"/lights/groups/{r.group}", json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "device_not_tested", response.text
    assert await snapshot() == before


async def test_e8_simulatie_toetst_groep_voor_verkeerde_modus(admin, group_rig):
    r = group_rig
    await change(r.members[1], compatibility="untested", tested_at=None)
    body = {**mapping(r, False), "mode": "climate"}
    async with sessionmaker()() as db:
        db.add(ButtonMapping(home_id=r.home, device_id=r.button, **body))
        await db.commit()
    before = await snapshot()
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": r.inv.B3, "event": "short_release"})
    step = response.json()["mappings"][0]["steps"][0]
    assert step["result"] == "refused" and step["code"] == "device_not_tested", response.text
    assert await snapshot() == before
    assert r.fake.calls == []


@pytest.mark.parametrize("flag", [False, None, "true"])
async def test_onvolledige_ledenmetadata_blijft_dicht(admin, group_rig, flag):
    r = group_rig
    async with sessionmaker()() as db:
        group = await db.get(Device, r.group)
        group.state = {**group.state, "members_complete": flag}
        await db.commit()
    response = await send(admin, r, False)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "device_not_tested", response.text


async def test_gate_uit_behoudt_oude_onbekende_groep(admin, group_rig):
    r = group_rig
    await set_gate(False)
    await change(r.group, state={}, compatibility="untested", tested_at=None)
    await change(r.scene, state={}, compatibility="untested", tested_at=None)
    response = await send(admin, r, True)
    assert response.status_code == 202, response.text
    await r.worker.process_commands({r.home})
    assert len(r.fake.calls) == 1 and (await commands(r))[0].status == "confirmed"


@pytest.mark.parametrize("info_only", [False, True])
async def test_inventaris_behoudt_leden_zonder_automatisch_getest(admin, info_only):
    from shc_integrations.base import DeviceInfo

    inv, model, worker = hue_inventory(), HueModel(), Worker()
    model.load(inv.full_resources())
    home = await new_home(admin, "Inventarisproef")
    integ = await add_integration(home, "hue")
    events = model.snapshot(datetime.now(UTC))
    await worker.handle_events(integ, [e for e in events if isinstance(e, DeviceInfo)] if info_only else events)
    async with sessionmaker()() as db:
        rows = list((await db.execute(select(Device).where(Device.home_id == home))).scalars())
        assert len(rows) >= 10
        assert all(d.compatibility == "untested" and d.tested_at is None for d in rows)
        group = next(d for d in rows if d.stable_identity == inv.G_LIVING)
        assert group.state.get("member_identities") == [inv.L1, inv.L2]
        assert group.state.get("members_complete") is True


async def test_onvolledige_hue_inventaris_weigert(admin, group_rig):
    r = group_rig
    del r.model.resources[r.inv.D1]
    await r.worker.handle_events(r.integ, [r.model.group_update(r.inv.G_LIVING, datetime.now(UTC))])
    before = await snapshot()
    response = await send(admin, r, False)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "device_not_tested", response.text
    assert await snapshot() == before
    assert r.fake.calls == []


@pytest.mark.parametrize("protected", [False, True])
async def test_hue_lid_met_alleen_eigenaarsrelatie_wordt_getoetst(admin, group_rig, protected):
    r = group_rig
    resources = r.inv.full_resources()
    owner = next(item for item in resources if item["id"] == r.inv.D2)
    owner["services"] = [s for s in owner["services"] if s["rid"] != r.inv.L2]
    r.model.load(resources)
    await r.worker.handle_events(r.integ, r.model.snapshot(datetime.now(UTC)))
    if protected:
        await protect(r.home, r.members[1])
    else:
        await change(r.members[1], compatibility="untested", tested_at=None)

    response = await send(admin, r, False)
    await r.worker.process_commands({r.home})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == ("policy_denied" if protected else "device_not_tested")
    assert not r.fake.calls and not await commands(r)
    group = r.model.group_state(r.inv.G_LIVING)
    assert group["member_identities"] == [r.inv.L1, r.inv.L2]
    assert group["members_complete"] is False


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("change_kind", ["gelijk", "ongetest", "beschermd"])
async def test_hue_toetst_actuele_bridge_voor_schrijven(admin, group_rig, scene, change_kind):
    r = group_rig
    resources = {item["id"]: item for item in r.inv.full_resources()}
    requests = []

    def bridge(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/clip/v2/resource":
            return httpx.Response(200, json={"data": list(resources.values()), "errors": []})
        back = {"id": request.url.path.rsplit("/", 1)[-1], "on": {"on": True}}
        return httpx.Response(200, json={"data": [back], "errors": []})

    adapter = HueAdapter({"host": "bridge.test"}, {"application_key": "proef"},
                         http_factory=lambda base, _: httpx.AsyncClient(
                             base_url=base, transport=httpx.MockTransport(bridge)))
    r.worker._adapter_for = lambda _: adapter
    assert (await send(admin, r, scene)).status_code == 202
    if change_kind != "gelijk":
        async with sessionmaker()() as db:
            new = await db.scalar(select(Device.id).where(
                Device.home_id == r.home, Device.stable_identity == r.inv.L3))
        if change_kind == "beschermd":
            await protect(r.home, new)
        else:
            await change(new, compatibility="untested", tested_at=None)
        resources[r.inv.ROOM_LIVING]["children"].append({"rid": r.inv.D3, "rtype": "device"})
    try:
        await r.worker.process_commands({r.home})
    finally:
        await adapter.close()
    writes = [path for method, path in requests if method == "PUT"]
    expected = [f"/clip/v2/resource/light/{lamp}" for lamp in (r.inv.L1, r.inv.L2)]
    assert writes == (expected if change_kind == "gelijk" else [])
    assert [row.status for row in await commands(r)] == ["confirmed" if change_kind == "gelijk" else "denied"]


@pytest.mark.parametrize("scene", [False, True], ids=["groep", "scene"])
@pytest.mark.parametrize("late_change", [False, True], ids=["derde_lid_getoetst", "derde_lid_na_lezing"])
async def test_hue_schrijft_alleen_naar_gelezen_en_getoetste_lampen(admin, group_rig, scene, late_change):
    r = group_rig
    resources = {item["id"]: item for item in r.inv.full_resources()}
    room_members = resources[r.inv.ROOM_LIVING]["children"]
    scene_actions = resources[r.inv.S_EVENING]["actions"] = [
        {"target": {"rid": lamp, "rtype": "light"}, "action": {"on": {"on": True}}}
        for lamp in (r.inv.L1, r.inv.L2)
    ]
    writes, affected = [], []
    readings = 0

    def add_member():
        room_members.append({"rid": r.inv.D3, "rtype": "device"})
        scene_actions.append({"target": {"rid": r.inv.L3, "rtype": "light"}, "action": {"on": {"on": True}}})

    def bridge(request):
        nonlocal readings
        path = request.url.path
        if path == "/clip/v2/resource":
            readings += 1
            snapshot = httpx.Response(200, json={"data": list(resources.values()), "errors": []})
            if late_change:
                add_member()
            return snapshot
        kind, identity = path.split("/")[-2:]
        if request.method == "PUT":
            body = json.loads(request.content)
            writes.append((kind, identity, body))
            if kind == "scene":
                affected.extend(action["target"]["rid"] for action in scene_actions)
            elif kind == "grouped_light":
                lamp_of = {r.inv.D1: r.inv.L1, r.inv.D2: r.inv.L2, r.inv.D3: r.inv.L3}
                affected.extend(lamp_of[member["rid"]] for member in room_members)
            else:
                affected.append(identity)
                resources[identity].update(body)
        return httpx.Response(200, json={"data": [resources[identity]], "errors": []})

    if late_change:
        async with sessionmaker()() as db:
            third = await db.scalar(select(Device.id).where(
                Device.home_id == r.home, Device.stable_identity == r.inv.L3))
        await protect(r.home, third)
    else:
        add_member()
    adapter = HueAdapter({"host": "bridge.test"}, {"application_key": "proef"},
                         http_factory=lambda base, _: httpx.AsyncClient(
                             base_url=base, transport=httpx.MockTransport(bridge)))
    r.worker._adapter_for = lambda _: adapter
    assert (await send(admin, r, scene)).status_code == 202
    try:
        await r.worker.process_commands({r.home})
    finally:
        await adapter.close()
    expected = [r.inv.L1, r.inv.L2] if late_change else [r.inv.L1, r.inv.L2, r.inv.L3]
    assert readings == 1
    assert affected == expected
    assert writes == [("light", lamp, {"on": {"on": True}}) for lamp in expected]
    assert [row.status for row in await commands(r)] == ["confirmed"]


@pytest.mark.parametrize("kind", ["light", "scene"])
@pytest.mark.parametrize("afwijkend", [False, True])
async def test_opgeslagen_hue_doel_is_gebonden_aan_commandoregel(group_rig, kind, afwijkend):
    r = group_rig
    target = r.members[0] if kind == "light" else r.scene
    actual_id = r.inv.L1 if kind == "light" else r.inv.S_EVENING
    action = "set_light" if kind == "light" else "recall_scene"
    writes = []

    def bridge(request):
        if request.method == "PUT":
            writes.append(request.url.path)
        if request.url.path == "/clip/v2/resource":
            return httpx.Response(200, json={"data": r.inv.full_resources(), "errors": []})
        return httpx.Response(200, json={"data": [{"id": request.url.path.rsplit("/", 1)[-1], "on": {"on": True}}],
                                         "errors": []})

    async with sessionmaker()() as db:
        db.add(CommandRecord(home_id=r.home, device_id=target, status="queued", action=action, source="user",
                             actor="proef", params={"id": r.inv.L2 if afwijkend else actual_id, "on": True}))
        await db.commit()
    adapter = HueAdapter({"host": "bridge.test"}, {"application_key": "proef"},
                         http_factory=lambda base, _: httpx.AsyncClient(
                             base_url=base, transport=httpx.MockTransport(bridge)))
    r.worker._adapter_for = lambda _: adapter
    try:
        await r.worker.process_commands({r.home})
    finally:
        await adapter.close()
    lamps = [actual_id] if kind == "light" else [r.inv.L1, r.inv.L2]
    assert writes == ([] if afwijkend else [f"/clip/v2/resource/light/{lamp}" for lamp in lamps])
    assert [row.status for row in await commands(r)] == ["denied" if afwijkend else "confirmed"]


@pytest.mark.parametrize("scope", ["home", "provider"])
async def test_andere_bron_bewijst_geen_groepslid(admin, group_rig, scope):
    r = group_rig
    if scope == "home":
        await change(r.members[1], home_id=await new_home(admin, "Andere woning"))
    else:
        await change(r.members[1], provider="andere_bron")
    before = await snapshot()
    response = await send(admin, r, False)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "device_not_tested", response.text
    assert await snapshot() == before


async def test_onbekend_lid_verbergt_bescherming_niet(admin, group_rig):
    r = group_rig
    await protect(r.home, r.members[1])
    await change(r.members[0], compatibility="untested", tested_at=None)
    async with sessionmaker()() as db:
        group = await db.get(Device, r.group)
        group.state = {**group.state, "member_identities": [r.inv.L1, r.inv.L2, 42], "members_complete": False}
        await db.commit()
    before = await snapshot()
    response = await send(admin, r, True)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "policy_denied", response.text
    assert await snapshot() == before
    assert r.fake.calls == []


async def test_scene_leest_groepswoning_vers_voor_afscherming(admin, group_rig):
    r = group_rig
    other_home = await new_home(admin, "Verplaatste groepsproef")
    async with sessionmaker()() as db:
        scene = await db.get(Device, r.scene)
        cached_group = await db.get(Device, r.group)
        assert cached_group.home_id == r.home
        async with sessionmaker()() as other:
            for did in [r.group, *r.members]:
                (await other.get(Device, did)).home_id = other_home
            await other.commit()
        before = await snapshot()
        with pytest.raises(PolicyDenied) as caught:
            await queue_command(db, r.home, scene, "recall_scene", {}, "user", "proef")
        assert caught.value.code == "device_not_tested"
        await db.commit()
    assert await snapshot() == before


async def test_patch_koppeling_toetst_groep_voor_oude_ongeldige_params(admin, app, group_rig):
    import httpx

    r = group_rig
    await protect(r.home, r.members[1])
    body = mapping(r, False)
    body["action"]["steps"][0]["params"] = 42  # Oude/importdata; niet via de opslagvalidatie opgebouwd.
    async with sessionmaker()() as db:
        row = ButtonMapping(home_id=r.home, device_id=r.button, **body)
        db.add(row)
        await db.commit()
        mid = row.id
    before = await snapshot()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url=admin.c.base_url, cookies=admin.c.cookies) as client:
        response = await client.patch(f"/api/v1/button-mappings/{mid}", json={"enabled": True}, headers=admin.h())
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied", response.text
    assert await snapshot() == before
    assert r.fake.calls == []
