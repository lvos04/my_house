"""Worker database tests with fake adapters (verbeterlijst 10, 11, 13, 14, 30, 42, log noise)."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from shc_api.db import sessionmaker
from shc_api.models import Alert, ClimateZone, CommandRecord, Device, SensorReading, ZoneLog
from shc_api.store import ingest_reading

from .conftest import Session


@pytest.fixture(autouse=True)
async def _default_home_first(home):
    """Make sure the shared default home exists before these tests add their own homes."""
    return home


CAPS = {"modes": ["off", "heat", "cool", "dry", "fan", "auto"], "min_c": 16, "max_c": 30, "step_c": 1.0, "fan": True}
ALL_DAY = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00"}
AUTO = ("schedule", "oem_fallback")  # following the schedule (oem_fallback: without external sensor)


class FakeUnit:
    """A Gree-like indoor unit: applies set_hvac params and reports its state back."""

    def __init__(self, mode="off", target=21.0):
        self.state = {"power": mode != "off", "mode": mode if mode != "off" else "heat", "target_c": target}
        self.state["fan"] = "auto"
        self.calls: list[dict] = []
        self.fail = False
        self.ignore = False  # answers, but does not apply (unconfirmed readback)

    @property
    def mode(self):
        return self.state["mode"] if self.state["power"] else "off"

    async def execute(self, command):
        from shc_integrations.base import CommandResult

        self.calls.append(dict(command.params))
        if self.fail:
            raise RuntimeError("Geen antwoord van de unit")
        p = command.params
        if not self.ignore:
            if "power" in p:
                self.state["power"] = p["power"]
            for k in ("mode", "target_c", "fan"):
                if p.get(k) is not None:
                    self.state[k] = p[k]
        return CommandResult(ok=True, confirmed=not self.ignore, state=dict(self.state))


class Rig:
    """One fresh home with its two zones on one outdoor unit, two fake units and a worker."""

    def __init__(self, admin, home_id, zones, units, fakes, worker):
        self.admin, self.home_id, self.zones, self.units, self.fakes, self.w = (
            admin,
            home_id,
            zones,
            units,
            fakes,
            worker,
        )

    @property
    def homes(self):
        return {self.home_id}

    async def readback(self, name, online=True):
        """Simulate a poll of the unit: write the fake's state to the device row."""
        fake = self.fakes[name]
        async with sessionmaker()() as db:
            d = await db.get(Device, self.units[name])
            d.state = {**(d.state or {}), **fake.state, "mode": fake.mode}
            d.online, d.last_seen = online, datetime.now(UTC)
            await db.commit()

    async def round(self):
        await self.w.climate_round(self.homes)

    async def run_commands(self):
        await self.w.process_commands(self.homes)

    async def zone(self, name) -> ClimateZone:
        async with sessionmaker()() as db:
            return await db.get(ClimateZone, self.zones[name])

    async def commands(self, name) -> list[CommandRecord]:
        async with sessionmaker()() as db:
            return list(
                (
                    await db.execute(
                        select(CommandRecord)
                        .where(CommandRecord.device_id == self.units[name])
                        .order_by(CommandRecord.created_at)
                    )
                ).scalars()
            )

    async def schedule(self, name, target, mode=None):
        block = {**ALL_DAY, "target_c": target}
        if mode:
            block["mode"] = mode
        r = await self.admin.put(f"/zones/{self.zones[name]}/schedule", json={"blocks": [block]})
        assert r.status_code == 200, r.text

    async def set_zone(self, name, **values):
        async with sessionmaker()() as db:
            await db.execute(update(ClimateZone).where(ClimateZone.id == self.zones[name]).values(**values))
            await db.commit()

    async def due_now(self):
        """Make retries that wait for their delay due immediately."""
        async with sessionmaker()() as db:
            await db.execute(
                update(CommandRecord)
                .where(CommandRecord.home_id == self.home_id, CommandRecord.status == "queued")
                .values(updated_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.commit()


async def make_rig(admin, living=("off", 21.0), bedroom=("off", 21.0), sensor=False) -> Rig:
    from shc_api.worker import Worker

    r = await admin.post("/homes", json={"name": f"Klimaattest {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    home_id = uuid.UUID(r.json()["id"])
    zones = {z["name"]: uuid.UUID(z["id"]) for z in (await admin.get(f"/homes/{home_id}/zones")).json()}
    fakes = {"Woonkamer": FakeUnit(*living), "Slaapkamer": FakeUnit(*bedroom)}
    units = {}
    async with sessionmaker()() as db:
        for name, fake in fakes.items():
            d = Device(
                home_id=home_id,
                provider="gree",
                kind="ac_unit",
                name=f"Airco {name}",
                stable_identity=uuid.uuid4().hex,
                state={"caps": CAPS, **fake.state, "mode": fake.mode},
                online=True,
                last_seen=datetime.now(UTC),
            )
            db.add(d)
            await db.flush()
            units[name] = d.id
        await db.commit()
    for name in fakes:
        body = {"unit_device_id": str(units[name])}
        if sensor:
            async with sessionmaker()() as db:
                s = Device(
                    home_id=home_id,
                    provider="zigbee",
                    kind="zigbee_sensor",
                    name=f"Sensor {name}",
                    stable_identity=uuid.uuid4().hex,
                    online=True,
                    last_seen=datetime.now(UTC),
                )
                db.add(s)
                await db.commit()
                units[f"sensor:{name}"] = s.id
            body["sensor_device_id"] = str(s.id)
        r = await admin.c.patch(f"/api/v1/zones/{zones[name]}", json=body, headers=admin.h())
        assert r.status_code == 200, r.text
    w = Worker()
    by_device = {units[n]: f for n, f in fakes.items()}
    w._adapter_for = lambda d: by_device.get(d.id)  # type: ignore[method-assign]
    rig = Rig(admin, home_id, zones, units, fakes, w)
    for name in fakes:
        await rig.readback(name)
    return rig


async def sensor_value(rig: Rig, name: str, value: float, at: datetime | None = None):
    async with sessionmaker()() as db:
        at = at or datetime.now(UTC)
        await ingest_reading(db, rig.units[f"sensor:{name}"], "temperature", value, "°C", at)
        await db.commit()


# --- item 14 / 42: two zones on one outdoor unit ----------------------------------------------


async def test_two_zones_on_one_outdoor_unit_are_decided_together(admin):
    rig = await make_rig(admin)
    await rig.schedule("Woonkamer", 21, "heat")
    await rig.schedule("Slaapkamer", 24, "cool")
    await rig.round()
    queued = [c for n in ("Woonkamer", "Slaapkamer") for c in await rig.commands(n)]
    assert len(queued) == 1, "only one of two conflicting zones may start"
    winner = "Slaapkamer" if queued[0].device_id == rig.units["Slaapkamer"] else "Woonkamer"
    loser = "Woonkamer" if winner == "Slaapkamer" else "Slaapkamer"
    assert (await rig.zone(loser)).last_decision.endswith("mode_blocked")
    # The API sees the pending command of the other unit (effective mode), under the same lock.
    want = "heat" if loser == "Woonkamer" else "cool"
    r = await admin.post(f"/zones/{rig.zones[loser]}/command", json={"mode": want})
    assert r.status_code == 409 and "buitenunit" in r.json()["detail"]["message"]
    # A conflicting command that slipped into the queue is refused right before execution.
    async with sessionmaker()() as db:
        db.add(
            CommandRecord(
                home_id=rig.home_id,
                device_id=rig.units[loser],
                zone_id=rig.zones[loser],
                action="set_hvac",
                params={"mode": want, "target_c": 22},
                source="button",
                actor="test",
            )
        )
        await db.commit()
    await rig.run_commands()
    assert (await rig.commands(winner))[-1].status == "confirmed"
    late = (await rig.commands(loser))[-1]
    assert late.status == "denied" and "buitenunit" in late.detail
    assert rig.fakes[loser].calls == []


async def test_offline_and_auto_units_still_block_conflicting_modes(admin):
    rig = await make_rig(admin, living=("auto", 22.0))
    r = await admin.post(f"/zones/{rig.zones['Slaapkamer']}/command", json={"mode": "heat"})
    assert r.status_code == 409 and "automatisch" in r.json()["detail"]["message"]
    rig.fakes["Woonkamer"].state.update(mode="heat")
    await rig.readback("Woonkamer", online=False)  # offline unit keeps its last known mode for a while
    r = await admin.post(f"/zones/{rig.zones['Slaapkamer']}/command", json={"mode": "cool"})
    assert r.status_code == 409
    z = (await admin.get(f"/zones/{rig.zones['Slaapkamer']}")).json()
    assert {b["mode"] for b in z["blocked_modes"]} == {"cool", "dry", "auto"}


# --- item 11 / 42: a failed command followed by a new round ------------------------------------


async def test_failed_command_retries_alerts_and_keeps_control(admin):
    rig = await make_rig(admin)
    await rig.schedule("Woonkamer", 21, "heat")
    rig.fakes["Woonkamer"].fail = True
    await rig.round()
    cmd = (await rig.commands("Woonkamer"))[-1]
    assert cmd.params == {"mode": "heat", "target_c": 21.0}
    for _ in range(3):
        await rig.run_commands()
        await rig.due_now()
    assert len(rig.fakes["Woonkamer"].calls) == 3
    cmds = await rig.commands("Woonkamer")
    assert len(cmds) == 1 and cmds[0].status == "failed" and cmds[0].attempts == 3
    z = await rig.zone("Woonkamer")
    assert z.control in AUTO and z.pending_command_id is None
    assert z.last_commanded.get("mode") == "off", "baseline only changes after a confirmed readback"
    async with sessionmaker()() as db:
        alert = (
            await db.execute(select(Alert).where(Alert.key == f"command:{z.id}", Alert.resolved_at.is_(None)))
        ).scalar_one()
        assert "niet aangepast" in alert.detail
    # New rounds: the unit is still off, which is NOT taken for the remote control.
    for _ in range(3):
        await rig.readback("Woonkamer")
        await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.control in AUTO and z.override_source is None
    # The unit answers again: the next engine command is confirmed and the alert resolved.
    rig.fakes["Woonkamer"].fail = False
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].mode == "heat"
    z = await rig.zone("Woonkamer")
    assert z.last_commanded["mode"] == "heat" and z.control in AUTO
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(Alert).where(Alert.key == f"command:{z.id}", Alert.resolved_at.is_(None)))
        ).scalar_one_or_none() is None


async def test_unconfirmed_command_is_confirmed_by_a_later_readback(admin):
    rig = await make_rig(admin, living=("heat", 21.0))
    await rig.schedule("Woonkamer", 21, "heat")
    await rig.round()  # adopts the baseline
    r = await admin.post(f"/zones/{rig.zones['Woonkamer']}/command", json={"target_c": 23})
    assert r.status_code == 202
    rig.fakes["Woonkamer"].ignore = True  # unit answers but shows the old state at first
    await rig.run_commands()
    cmd = (await rig.commands("Woonkamer"))[-1]
    assert cmd.status == "sent"
    rig.fakes["Woonkamer"].ignore = False
    rig.fakes["Woonkamer"].state["target_c"] = 23.0  # applied a moment later
    await rig.readback("Woonkamer")
    await rig.round()
    cmd = (await rig.commands("Woonkamer"))[-1]
    z = await rig.zone("Woonkamer")
    assert cmd.status == "confirmed" and z.last_commanded["target_c"] == 23.0
    assert z.control == "manual" and z.override_source == "app"


# --- item 10 / 42: an override that runs past its expiry ---------------------------------------


async def test_remote_override_expires_back_to_schedule_without_loop(admin):
    rig = await make_rig(admin, living=("heat", 21.0))
    await rig.schedule("Woonkamer", 21, "heat")
    await rig.round()  # baseline = heat 21
    assert (await rig.zone("Woonkamer")).last_commanded["target_c"] == 21.0
    rig.fakes["Woonkamer"].state["target_c"] = 24.0  # someone uses the remote control
    await rig.readback("Woonkamer")
    await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.control in AUTO and z.mismatch_count == 1, "one readback is not enough"
    await rig.round()  # same readback again: still not enough
    assert (await rig.zone("Woonkamer")).control in AUTO
    await rig.readback("Woonkamer")
    await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.control == "manual" and z.override_source == "remote_control"
    assert z.last_commanded["target_c"] == 24.0, "the observed state is the new baseline"
    assert await rig.commands("Woonkamer") == []
    # Two hours later the override expires: back to schedule, no re-detection.
    await rig.set_zone("Woonkamer", override_until=datetime.now(UTC) - timedelta(minutes=1))
    await rig.readback("Woonkamer")
    await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.control in AUTO and z.override_until is None
    cmd = (await rig.commands("Woonkamer"))[-1]
    assert cmd.source == "engine" and cmd.params == {"mode": "heat", "target_c": 23.0}
    await rig.run_commands()
    for _ in range(3):
        await rig.readback("Woonkamer")
        await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.control in AUTO and z.override_source is None
    async with sessionmaker()() as db:
        lines = (await db.execute(select(ZoneLog.message).where(ZoneLog.zone_id == z.id))).scalars().all()
    assert sum("afstandsbediening" in m for m in lines) == 1


# --- item 30 / 42: step 1.0 with even and odd targets -----------------------------------------


async def test_step_one_correction_with_even_and_odd_targets(admin):
    rig = await make_rig(admin, living=("heat", 20.0), bedroom=("heat", 21.0), sensor=True)
    await rig.schedule("Woonkamer", 20, "heat")
    await rig.schedule("Slaapkamer", 21, "heat")
    await sensor_value(rig, "Woonkamer", 19.0)
    await sensor_value(rig, "Slaapkamer", 20.0)
    await rig.round()
    assert (await rig.commands("Woonkamer"))[-1].params == {"mode": "heat", "target_c": 21.0}
    assert (await rig.commands("Slaapkamer"))[-1].params == {"mode": "heat", "target_c": 22.0}
    assert (await rig.zone("Woonkamer")).correction_c == 1.0
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].state["target_c"] == 21.0 and rig.fakes["Slaapkamer"].state["target_c"] == 22.0


# --- item 12 / 42: schedule blocks without a mode ------------------------------------------------


async def test_block_without_mode_never_starts_and_keeps_running_mode(admin):
    rig = await make_rig(admin)
    await rig.schedule("Woonkamer", 22)  # no mode
    await rig.round()
    assert await rig.commands("Woonkamer") == []
    assert (await rig.zone("Woonkamer")).last_decision.endswith("keep_off")
    # The user starts the unit in a mode; after resume the block keeps that mode.
    r = await admin.post(f"/zones/{rig.zones['Woonkamer']}/command", json={"mode": "cool", "target_c": 25})
    assert r.status_code == 202
    await rig.run_commands()
    assert (await admin.post(f"/zones/{rig.zones['Woonkamer']}/resume")).status_code == 200
    await rig.set_zone("Woonkamer", last_command_at=datetime.now(UTC) - timedelta(hours=1))  # past min interval
    await rig.readback("Woonkamer")
    await rig.round()  # settle after the command
    await rig.readback("Woonkamer")
    await rig.round()
    cmd = (await rig.commands("Woonkamer"))[-1]
    assert cmd.source == "engine" and cmd.params == {"mode": "cool", "target_c": 24.0}


# --- item 13: a temperature change never switches an off unit on ---------------------------------


async def test_temperature_only_on_off_unit_is_refused(admin):
    rig = await make_rig(admin)
    zid = rig.zones["Woonkamer"]
    r = await admin.post(f"/zones/{zid}/command", json={"target_c": 22})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "mode_required"
    assert "Kies eerst een modus" in r.json()["detail"]["message"]
    r = await admin.post(f"/zones/{zid}/command", json={"fan": "high"})
    assert r.status_code == 409
    z = (await admin.get(f"/zones/{zid}")).json()
    assert z["setpoint_adjustable"] is False
    # with an explicit mode it starts; the adapter gets power on only then
    assert (await admin.post(f"/zones/{zid}/command", json={"mode": "heat", "target_c": 22})).status_code == 202
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].calls[-1] == {"power": True, "mode": "heat", "target_c": 22.0}
    assert (await admin.post(f"/zones/{zid}/command", json={"fan": "low"})).status_code == 202
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].calls[-1] == {"fan": "low"}


# --- item 14 / 42: engine command dropped after the user turned the zone off --------------------


async def test_engine_command_discarded_after_user_turned_zone_off(admin):
    rig = await make_rig(admin)
    await rig.schedule("Woonkamer", 21, "heat")
    await rig.round()
    engine_cmd = (await rig.commands("Woonkamer"))[-1]
    assert engine_cmd.source == "engine" and engine_cmd.status == "queued"
    r = await admin.post(f"/zones/{rig.zones['Woonkamer']}/command", json={"mode": "off"})
    assert r.status_code == 202
    # Race: an engine command that ends up queued after the user's "off".
    async with sessionmaker()() as db:
        db.add(
            CommandRecord(
                home_id=rig.home_id,
                device_id=rig.units["Woonkamer"],
                zone_id=rig.zones["Woonkamer"],
                action="set_hvac",
                params={"mode": "heat", "target_c": 21},
                source="engine",
                actor="klimaatregelaar",
                created_at=datetime.now(UTC) + timedelta(milliseconds=5),
            )
        )
        await db.commit()
    await rig.run_commands()
    cmds = await rig.commands("Woonkamer")
    assert [c.status for c in cmds] == ["cancelled", "confirmed", "cancelled"]
    assert rig.fakes["Woonkamer"].calls == [{"power": False}]
    z = (await admin.get(f"/zones/{rig.zones['Woonkamer']}")).json()
    assert z["control"] == "off" and z["can_resume"] is True and z["mode"] == "off"


# --- item 11 / 18 / 42: resume from off; a viewer may not resume -------------------------------


async def test_viewer_cannot_resume_and_resume_works_from_off(admin, app):
    rig = await make_rig(admin, living=("heat", 21.0))
    zid = rig.zones["Woonkamer"]
    assert (await admin.post(f"/zones/{zid}/command", json={"mode": "off"})).status_code == 202
    name = f"kijk{uuid.uuid4().hex[:6]}"
    r = await admin.post("/auth/users", json={"username": name, "password": "kijker-wachtwoord-789", "role": "viewer"})
    assert r.status_code == 200, r.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"username": name, "password": "kijker-wachtwoord-789"})
        v = Session(c, r.json()["csrf_token"])
        assert (await v.get(f"/zones/{zid}")).json()["can_resume"] is True
        r = await v.post(f"/zones/{zid}/resume")
        assert r.status_code == 403
    assert (await admin.get(f"/zones/{zid}")).json()["control"] == "off"
    r = await admin.post(f"/zones/{zid}/resume")
    assert r.status_code == 200 and r.json()["control"] == "schedule" and r.json()["can_resume"] is False


# --- log noise, zone-log retention -----------------------------------------------------------------


async def test_zone_log_only_grows_when_the_decision_changes(admin):
    rig = await make_rig(admin, living=("heat", 21.0))
    await rig.schedule("Woonkamer", 21, "heat")
    zid = rig.zones["Woonkamer"]

    async def count():
        async with sessionmaker()() as db:
            return (await db.execute(select(func.count()).select_from(ZoneLog).where(ZoneLog.zone_id == zid))).scalar()

    await rig.round()
    before = await count()
    for _ in range(5):
        await rig.readback("Woonkamer")
        await rig.round()
    assert await count() == before
    old = datetime.now(UTC) - timedelta(days=400)
    async with sessionmaker()() as db:
        db.add(ZoneLog(zone_id=zid, kind="engine", message="heel oud", at=old))
        await db.commit()
    # Old lines go in the retention round (period from the app), not in the climate round.
    await rig.round()
    await rig.w.retention_round()
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(func.count()).select_from(ZoneLog).where(ZoneLog.message == "heel oud"))
        ).scalar() == 0


# --- item 45: watchdog ---------------------------------------------------------------------------------


async def test_watchdog_alerts_when_room_does_not_warm_up(admin):
    rig = await make_rig(admin, living=("heat", 22.0), sensor=True)
    zid = rig.zones["Woonkamer"]
    r = await admin.put(f"/zones/{zid}/policy", {"watchdog_minutes": 30, "watchdog_min_progress_c": 0.3})
    assert r.status_code == 200 and r.json()["policy"]["watchdog_minutes"] == 30
    await rig.schedule("Woonkamer", 22, "heat")
    now = datetime.now(UTC)
    await sensor_value(rig, "Woonkamer", 19.0, now - timedelta(minutes=45))
    await sensor_value(rig, "Woonkamer", 19.1, now)
    await rig.set_zone("Woonkamer", last_command_at=now)  # no correction commands during this test
    await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.watchdog_since is not None
    await rig.set_zone("Woonkamer", watchdog_since=now - timedelta(minutes=40))
    await rig.readback("Woonkamer")
    await rig.round()
    async with sessionmaker()() as db:
        alert = (
            await db.execute(select(Alert).where(Alert.key == f"watchdog:{zid}", Alert.resolved_at.is_(None)))
        ).scalar_one()
    assert "reageert niet" in alert.title
    # The room warms up: the next window resolves the alert.
    await sensor_value(rig, "Woonkamer", 21.9, datetime.now(UTC))
    await rig.readback("Woonkamer")
    await rig.round()  # inside the band now: watchdog stops and resolves
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(Alert).where(Alert.key == f"watchdog:{zid}", Alert.resolved_at.is_(None)))
        ).scalar_one_or_none() is None


async def unit_temperature(rig: Rig, name: str, value: float, at: datetime | None = None):
    """A reading of the unit's own room sensor (Gree "unit_temperature", stored as "temperature")."""
    async with sessionmaker()() as db:
        await ingest_reading(db, rig.units[name], "temperature", value, "°C", at or datetime.now(UTC))
        await db.commit()


async def test_watchdog_uses_the_units_own_temperature_without_a_zone_sensor(admin):
    """Item 45: a zone without an external sensor regulates on the unit's own sensor (oem_fallback); the
    watchdog the app shows ("Waakhond: na 30 min") must work there too, on the unit's room temperature."""
    rig = await make_rig(admin, living=("heat", 22.0), sensor=False)
    zid = rig.zones["Woonkamer"]
    assert (await admin.put(f"/zones/{zid}/policy", {"watchdog_minutes": 30})).status_code == 200
    await rig.schedule("Woonkamer", 22, "heat")
    now = datetime.now(UTC)
    await rig.set_zone("Woonkamer", last_command_at=now)
    await rig.round()
    assert (await rig.zone("Woonkamer")).watchdog_since is None  # no room temperature at all yet: no window

    await unit_temperature(rig, "Woonkamer", 18.5, now - timedelta(minutes=45))
    await unit_temperature(rig, "Woonkamer", 18.6, now)
    await rig.readback("Woonkamer")
    await rig.round()
    z = await rig.zone("Woonkamer")
    assert z.control == "oem_fallback" and z.watchdog_since is not None
    await rig.set_zone("Woonkamer", watchdog_since=now - timedelta(minutes=40))
    await rig.readback("Woonkamer")
    await rig.round()
    async with sessionmaker()() as db:
        alert = (
            await db.execute(select(Alert).where(Alert.key == f"watchdog:{zid}", Alert.resolved_at.is_(None)))
        ).scalar_one()
    assert "reageert niet" in alert.title
    assert "gemeten door de airco zelf" in alert.detail and "18.5" in alert.detail
    # A stale unit reading (older than stale_after_s, 30 min) pauses the watchdog and resolves its alert,
    # just like a stale external sensor does.
    async with sessionmaker()() as db:
        await db.execute(
            update(SensorReading)
            .where(SensorReading.device_id == rig.units["Woonkamer"])
            .values(observed_at=SensorReading.observed_at - timedelta(hours=2))
        )
        await db.commit()
    await rig.readback("Woonkamer")
    await rig.round()
    assert (await rig.zone("Woonkamer")).watchdog_since is None
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(Alert).where(Alert.key == f"watchdog:{zid}", Alert.resolved_at.is_(None)))
        ).scalar_one_or_none() is None
