"""Meerstapsknoppen via opslag, simulatie, oude koppelingen en adapteruitvoering."""

import uuid
from datetime import UTC, datetime

import pytest
from shc_integrations.base import ButtonEvent

from shc_api.db import sessionmaker
from shc_api.models import ButtonMapping, ClimateZone

from .boiler_support import protect
from .test_api import add_device
from .test_inbedrijfstelling_groepen import change, commands
from .test_inbedrijfstelling_groepen import group_rig as group_rig
from .test_inbedrijfstelling_handhaving import commissioning_state as commissioning_state
from .test_inbedrijfstelling_handhaving import set_gate, snapshot
from .test_ws_climate_worker import CAPS


@pytest.fixture
async def button_rig(admin, group_rig):
    r = group_rig
    r.zone = uuid.UUID((await admin.get(f"/homes/{r.home}/zones")).json()[0]["id"])
    r.unit = await add_device(str(r.home), "ac_unit", "Klimaatdoel", state={
        "caps": CAPS, "mode": "heat", "power": False, "target_c": 20,
    })
    await change(r.unit, compatibility="tested", tested_at=datetime.now(UTC))
    async with sessionmaker()() as db:
        zone = await db.get(ClimateZone, r.zone)
        zone.unit_device_id = r.unit
        zone.control = "off"
        await db.commit()
    return r


def combined(r, zone_action):
    return {"button": r.inv.B3, "event": "short_release", "mode": "combined", "combined_confirmed": True,
            "action": {"steps": [
                {"device_id": str(r.members[0]), "action": "toggle"},
                {"zone_id": str(r.zone), **zone_action},
            ]}}


async def block_target(r, condition):
    if condition == "untested":
        await change(r.unit, compatibility="untested", tested_at=None)
        return "device_not_tested"
    if condition == "protected":
        await change(r.members[0], compatibility="untested", tested_at=None)
        await protect(r.home, r.unit)
        return "policy_denied"
    if condition == "gate_uit":
        await set_gate(False)
        await change(r.unit, compatibility="untested", tested_at=None)
    return None


@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize("condition", ["tested", "untested", "protected"])
async def test_knopopslag_meerdere_doelen(admin, button_rig, method, condition):
    r = button_rig
    body = combined(r, {"action": "resume"})
    simple = {"button": r.inv.B3, "event": "repeat", "mode": "lights",
              "action": {"device_id": str(r.members[1]), "action": "toggle"}}
    saved = await admin.post(f"/buttons/{r.button}/mappings", json=simple)
    assert saved.status_code == 200, saved.text
    mid = saved.json()["mapping"][0]["id"]
    code = await block_target(r, condition)
    before = await snapshot()
    if method == "put":
        simple["action"] = {"device_id": str(r.group), "action": "toggle"}
        response = await admin.put(f"/buttons/{r.button}/mappings", json=[simple, body])
    elif method == "patch":
        response = await admin.c.patch(f"/api/v1/button-mappings/{mid}", json=body, headers=admin.h())
    else:
        response = await admin.post(f"/buttons/{r.button}/mappings", json=body)
    assert response.status_code == (409 if code else 200), response.text
    if code:
        assert response.json()["detail"]["code"] == code, response.text
        assert await snapshot() == before
    else:
        saved = next(m for m in response.json()["mapping"] if m["event"] == "short_release")
        assert len(saved["action"]["steps"]) == 2
    assert await commands(r) == []
    assert r.fake.calls == []


@pytest.mark.parametrize("zone_action", [{"action": "resume"}, {"mode": "heat", "target_c": 23}],
                         ids=["hervatten", "bedienen"])
@pytest.mark.parametrize("condition", ["tested", "untested", "protected", "gate_uit"])
@pytest.mark.parametrize("split_mappings", [False, True], ids=["een-koppeling", "oude-dubbele-koppeling"])
async def test_meerstapsknop_simulatie_event_en_replay(admin, button_rig, zone_action, condition, split_mappings):
    r = button_rig
    body = combined(r, zone_action)
    async with sessionmaker()() as db:
        # Oude of geïmporteerde koppeling, zodat de uitvoeringscontrole zelf wordt bereikt.
        if split_mappings:
            for mode, step in zip(("lights", "climate"), body["action"]["steps"], strict=True):
                db.add(ButtonMapping(home_id=r.home, device_id=r.button,
                                     **{**body, "mode": mode, "action": step}))
        else:
            db.add(ButtonMapping(home_id=r.home, device_id=r.button, **body))
        await db.commit()
    code = await block_target(r, condition)
    before = await snapshot()
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": r.inv.B3, "event": body["event"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["would_run"] is (code is None), result
    steps = [step for mapping in result["mappings"] for step in mapping["steps"]]
    assert len(steps) == 2
    if code:
        assert [s["result"] for s in steps] == ["refused", "refused"], steps
        assert {s["code"] for s in steps} == {code}
    else:
        assert [s["result"] for s in steps] == ["would_run", "would_run"], steps
    assert await snapshot() == before
    assert r.fake.calls == []

    event = ButtonEvent(r.inv.D_DIMMER, r.inv.B3, body["event"], datetime.now(UTC), event_id=uuid.uuid4().hex)
    await r.worker.handle_events(r.integ, [event])
    after = await snapshot()
    if code:
        # De knopstatus en deduplicatie blijven bewaard. Bedieningsgegevens blijven bytegelijk.
        assert after["commands"] == before["commands"], "Een geweigerde knopdruk zette toch een opdracht klaar."
        assert after["processed_events"] != before["processed_events"]
        assert {k: v for k, v in after.items() if k not in {"devices", "processed_events"}} == {
            k: v for k, v in before.items() if k not in {"devices", "processed_events"}
        }
    await r.worker.handle_events(r.integ, [event])
    assert await snapshot() == after
    await r.worker.process_commands({r.home})
    rows = await commands(r)
    expected = 0 if code else (1 if zone_action.get("action") == "resume" else 2)
    assert len(rows) == len(r.fake.calls) == expected
    assert all(row.status == "confirmed" for row in rows)
    async with sessionmaker()() as db:
        zone = await db.get(ClimateZone, r.zone)
        expected_control = "off" if code else ("schedule" if zone_action.get("action") == "resume" else "manual")
        assert zone.control == expected_control
