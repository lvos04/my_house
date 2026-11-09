"""Climate API: zone view (items 11, 31), schedule validation (12), policy, profiles and experiments (45)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import Alert, ClimateZone, Experiment
from shc_api.store import ingest_reading

from .test_ws_climate_worker import AUTO, make_rig


@pytest.fixture(autouse=True)
async def _default_home_first(home):
    """Make sure the shared default home exists before these tests add their own homes."""
    return home


async def _new_home(admin) -> tuple[str, dict]:
    r = await admin.post("/homes", json={"name": f"Profieltest {uuid.uuid4().hex[:6]}"})
    hid = r.json()["id"]
    zones = {z["name"]: z for z in (await admin.get(f"/homes/{hid}/zones")).json()}
    return hid, zones


# --- item 31: zones without a unit ----------------------------------------------------------


async def test_zone_without_unit_is_neutral_and_shows_sensor_values(admin):
    hid, zones = await _new_home(admin)
    z = zones["Woonkamer"]
    assert z["unit_linked"] is False and z["unit_status"] == "not_linked"
    assert z["notice_level"] == "info" and "Nog geen airco gekoppeld" in z["notice"]
    assert z["can_resume"] is False and z["setpoint_adjustable"] is False and z["capabilities"]["modes"] == []
    # a sensor-only zone shows the sensor's values
    from shc_api.models import Device

    async with sessionmaker()() as db:
        s = Device(
            home_id=uuid.UUID(hid),
            provider="zigbee",
            kind="zigbee_sensor",
            name="Sensor",
            stable_identity=uuid.uuid4().hex,
            online=True,
            last_seen=datetime.now(UTC),
        )
        db.add(s)
        await db.flush()
        await ingest_reading(db, s.id, "temperature", 20.4, "°C", datetime.now(UTC))
        await ingest_reading(db, s.id, "humidity", 48.0, "%", datetime.now(UTC))
        await db.commit()
    r = await admin.c.patch(f"/api/v1/zones/{z['id']}", json={"sensor_device_id": str(s.id)}, headers=admin.h())
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["temperature"]["value"] == 20.4 and v["humidity"]["value"] == 48.0
    assert v["unit_status"] == "not_linked" and "sensorwaarden" in v["notice"]
    r = await admin.post(f"/zones/{z['id']}/command", json={"mode": "heat"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "unsupported"
    # a device of another home cannot be linked
    r = await admin.c.patch(f"/api/v1/zones/{z['id']}", json={"unit_device_id": str(uuid.uuid4())}, headers=admin.h())
    assert r.status_code == 422


async def test_offline_unit_is_a_warning_not_a_missing_unit(admin):
    rig = await make_rig(admin, living=("heat", 21.0))
    await rig.readback("Woonkamer", online=False)
    z = (await admin.get(f"/zones/{rig.zones['Woonkamer']}")).json()
    assert z["unit_linked"] is True and z["unit_status"] == "offline" and z["notice_level"] == "warn"
    on = (await admin.get(f"/zones/{rig.zones['Slaapkamer']}")).json()
    assert on["unit_status"] == "online" and on["capabilities"]["fan"] is True and on["capabilities"]["step_c"] == 1.0


# --- item 12: schedule validation -------------------------------------------------------------


async def test_schedule_validation_and_mode_optional(admin):
    rig = await make_rig(admin)
    zid = rig.zones["Woonkamer"]
    ok = {
        "blocks": [
            {"days": [0, 1, 2, 3, 4], "start": "07:00", "target_c": 21, "mode": "heat", "label": "Ochtend"},
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "22:30", "target_c": 18, "label": "Nacht"},
        ]
    }
    r = await admin.put(f"/zones/{zid}/schedule", json=ok)
    assert r.status_code == 200 and r.json()["blocks"][1]["mode"] is None
    dup = {
        "blocks": [{"days": [0], "start": "07:00", "target_c": 20}, {"days": [0, 1], "start": "07:00", "target_c": 21}]
    }
    r = await admin.put(f"/zones/{zid}/schedule", json=dup)
    assert r.status_code == 422 and "maandag" in r.json()["detail"]["message"]
    r = await admin.put(f"/zones/{zid}/schedule", json={"blocks": [{"days": [1, 1], "start": "07:00", "target_c": 20}]})
    assert r.status_code == 422
    r = await admin.put(f"/zones/{zid}/schedule", json={"blocks": [{"days": [1], "start": "07:00", "target_c": 12}]})
    assert r.status_code == 422 and "bereik" in r.json()["detail"]["message"]
    z = (await admin.get(f"/zones/{zid}")).json()
    assert z["next_change"] is not None and "mode" in z["next_change"]


# --- policy ----------------------------------------------------------------------------------------


async def test_policy_is_partial_and_validated(admin):
    rig = await make_rig(admin)
    zid = rig.zones["Woonkamer"]
    r = await admin.put(
        f"/zones/{zid}/policy",
        {"energy_priority": "saving", "plausible_min_c": 0, "plausible_max_c": 35, "watchdog_minutes": 0},
    )
    assert r.status_code == 200, r.text
    pol = r.json()["policy"]
    assert pol["energy_priority"] == "saving" and pol["plausible_max_c"] == 35 and pol["band_c"] == 0.5
    assert r.json()["energy_priority"] == "saving"
    assert (await admin.put(f"/zones/{zid}/policy", {"plausible_min_c": 30, "plausible_max_c": 20})).status_code == 422
    assert (await admin.put(f"/zones/{zid}/policy", {"watchdog_minutes": 5})).status_code == 422
    assert (await admin.put(f"/zones/{zid}/policy", {"energy_priority": "turbo"})).status_code == 422


# --- item 45: comfort profiles ----------------------------------------------------------------------


async def test_profiles_crud_apply_and_follow(admin):
    rig = await make_rig(admin)
    hid, zid = rig.home_id, rig.zones["Woonkamer"]
    body = {
        "name": "Comfort",
        "target_c": 21,
        "band_c": 0.6,
        "mode": "heat",
        "energy_priority": "comfort",
        "schedule": [
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "07:00", "target_c": 21, "label": "Dag"},
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "23:00", "target_c": 18, "label": "Nacht"},
        ],
    }
    r = await admin.post(f"/homes/{hid}/profiles", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert (await admin.post(f"/homes/{hid}/profiles", json=body)).status_code == 409
    eco = (await admin.post(f"/homes/{hid}/profiles", json={"name": "Eco", "target_c": 19})).json()
    assert [p["name"] for p in (await admin.get(f"/homes/{hid}/profiles")).json()] == ["Comfort", "Eco"]
    # apply: schedule replaced, blocks without mode inherit the profile mode, band + priority set
    r = await admin.post(f"/zones/{zid}/profile", json={"profile_id": pid})
    assert r.status_code == 200, r.text
    z = r.json()
    assert z["profile"] == "Comfort" and z["profile_id"] == pid and z["energy_priority"] == "comfort"
    assert z["policy"]["band_c"] == 0.6
    sched = (await admin.get(f"/zones/{zid}/schedule")).json()
    assert sched["profile_id"] == pid and {b["mode"] for b in sched["blocks"]} == {"heat"}
    assert (await admin.get(f"/profiles/{pid}")).json()["zone_ids"] == [str(zid)]
    # editing the profile updates the zones that follow it
    body["schedule"][1]["target_c"] = 17
    assert (await admin.put(f"/profiles/{pid}", json=body)).status_code == 200
    sched = (await admin.get(f"/zones/{zid}/schedule")).json()
    assert 17 in [b["target_c"] for b in sched["blocks"]]
    # a profile without schedule becomes one constant block
    await admin.post(f"/zones/{rig.zones['Slaapkamer']}/profile", json={"profile_id": eco["id"]})
    s2 = (await admin.get(f"/zones/{rig.zones['Slaapkamer']}/schedule")).json()["blocks"]
    assert len(s2) == 1 and s2[0]["target_c"] == 19 and s2[0]["start"] == "00:00"
    # an own schedule detaches the profile
    await admin.put(f"/zones/{zid}/schedule", json={"blocks": [{"days": [0], "start": "08:00", "target_c": 20}]})
    z = (await admin.get(f"/zones/{zid}")).json()
    assert z["profile_id"] is None
    assert (await admin.delete(f"/profiles/{pid}")).status_code == 204
    assert (await admin.get(f"/profiles/{pid}")).status_code == 404


# --- item 45: experiments with profiles, auto-apply and auto-pause ------------------------------------


async def test_experiment_auto_apply_pause_and_result(admin):
    rig = await make_rig(admin, living=("heat", 20.0), sensor=True)
    hid, zid = rig.home_id, rig.zones["Woonkamer"]
    a = (await admin.post(f"/homes/{hid}/profiles", json={"name": "A", "target_c": 20, "mode": "heat"})).json()
    b = (await admin.post(f"/homes/{hid}/profiles", json={"name": "B", "target_c": 22, "mode": "heat"})).json()
    now = datetime.now(UTC)
    variants = [
        {
            "label": "Constant",
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "profile_id": a["id"],
        },
        {
            "label": "Warmer",
            "start": (now + timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=2)).isoformat(),
            "profile_id": b["id"],
        },
    ]
    bad = {
        "name": "X",
        "zone_id": str(zid),
        "auto_apply": True,
        "variants": [{**variants[0], "profile_id": None}, variants[1]],
    }
    assert (await admin.post(f"/homes/{hid}/experiments", json=bad)).status_code == 422
    r = await admin.post(
        f"/homes/{hid}/experiments",
        json={"name": "Warmte", "zone_id": str(zid), "auto_apply": True, "variants": variants},
    )
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    assert (await admin.delete(f"/profiles/{a['id']}")).status_code == 409
    for v in (19.8, 20.1):
        async with sessionmaker()() as db:
            await ingest_reading(db, rig.units["sensor:Woonkamer"], "temperature", v, "°C", datetime.now(UTC))
            await db.commit()
    await rig.round()
    e = (await admin.get(f"/experiments/{eid}")).json()
    assert e["status"] == "running" and e["current_variant"] == 0
    z = (await admin.get(f"/zones/{zid}")).json()
    assert z["profile_id"] == a["id"]
    assert len(e["rows"]) == 2 and "comfort_pct" in e["rows"][0]
    assert any("Slaapkamer" in c for c in e["confounders"])
    assert any("buitentemperatuursensor" in c for c in e["confounders"])
    # manual override pauses the experiment (and resuming is refused while the zone is manual)
    r = await admin.post(f"/zones/{zid}/command", json={"mode": "heat", "target_c": 23})
    assert r.status_code == 202
    await rig.round()
    e = (await admin.get(f"/experiments/{eid}")).json()
    assert e["status"] == "paused" and "handmatig" in e["paused_reason"]
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(Alert).where(Alert.key == f"experiment:{eid}", Alert.resolved_at.is_(None)))
        ).scalar_one()
    assert (await admin.post(f"/experiments/{eid}/resume")).status_code == 409
    assert (await admin.post(f"/zones/{zid}/resume")).status_code == 200
    r = await admin.post(f"/experiments/{eid}/resume")
    assert r.status_code == 200 and r.json()["status"] == "running"
    # missing data pauses it too
    async with sessionmaker()() as db:
        z = await db.get(ClimateZone, zid)
        z.policy = {**z.policy, "stale_after_s": 300}
        await db.commit()
    from shc_api.models import SensorReading

    async with sessionmaker()() as db:
        from sqlalchemy import update

        await db.execute(
            update(SensorReading)
            .where(SensorReading.device_id == rig.units["sensor:Woonkamer"])
            .values(observed_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await db.commit()
    await rig.round()
    e = (await admin.get(f"/experiments/{eid}")).json()
    assert e["status"] == "paused" and "ontbreken" in e["paused_reason"]
    assert (await admin.post(f"/experiments/{eid}/stop")).json()["status"] == "stopped"
    async with sessionmaker()() as db:
        assert (await db.get(Experiment, uuid.UUID(eid))).auto_apply is True


async def test_climate_roles_offer_outdoor_sensor(admin):
    hid, _ = await _new_home(admin)
    roles = (await admin.get(f"/homes/{hid}/climate/roles")).json()
    assert roles == [{"role": "outdoor_temp", "label": "Buitentemperatuur", "device_id": None, "device_name": None}]


async def test_resume_keeps_zone_following_schedule(admin):
    rig = await make_rig(admin, living=("heat", 21.0))
    zid = rig.zones["Woonkamer"]
    assert (await admin.post(f"/zones/{zid}/command", json={"target_c": 23})).status_code == 202
    z = (await admin.get(f"/zones/{zid}")).json()
    assert z["control"] == "manual" and z["can_resume"] is True and z["pending_command"]["status"] == "queued"
    r = await admin.post(f"/zones/{zid}/resume")
    assert r.json()["control"] in AUTO or r.json()["control"] == "schedule"
