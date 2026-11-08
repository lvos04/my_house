import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from shc_api import backup as bk
from shc_api.db import sessionmaker
from shc_api.models import (
    ClimateZone,
    CommandRecord,
    Device,
    DeviceBinding,
    LogicalRole,
    SensorReading,
)
from shc_api.store import ingest_reading, set_setting

from .conftest import ADMIN_PW, Session


async def add_device(home_id, kind, name, role=None, provider="test", state=None, online=True):
    async with sessionmaker()() as db:
        d = Device(
            home_id=uuid.UUID(home_id),
            provider=provider,
            kind=kind,
            name=name,
            stable_identity=uuid.uuid4().hex,
            state=state or {},
            online=online,
            last_seen=datetime.now(UTC),
        )
        db.add(d)
        await db.flush()
        if role:
            r = (
                await db.execute(select(LogicalRole).where(LogicalRole.home_id == d.home_id, LogicalRole.key == role))
            ).scalar_one()
            db.add(DeviceBinding(role_id=r.id, device_id=d.id, effective_from=datetime.now(UTC) - timedelta(days=400)))
        await db.commit()
        return d.id


# --- auth ------------------------------------------------------------------------


async def test_setup_only_once_and_no_default_password(client, admin):
    r = await client.post("/api/v1/auth/setup", json={"username": "x2", "password": "another long password"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "setup_done"


async def test_unauthenticated_blocked(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/v1/homes")).status_code == 401
        assert (await c.get("/api/v1/health")).status_code == 200


async def test_csrf_required_for_cookie_mutations(admin):
    r = await admin.c.post("/api/v1/homes", json={"name": "X"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf_failed"


async def test_login_rate_limit(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        codes = [
            (await c.post("/api/v1/auth/login", json={"username": "beheer", "password": "fout"})).status_code
            for _ in range(6)
        ]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    from shc_api.routers import auth

    auth._attempts.clear()


async def test_viewer_is_read_only(admin, home, app):
    import httpx

    r = await admin.post(
        "/auth/users", json={"username": "kijker", "password": "kijker-wachtwoord-123", "role": "viewer"}
    )
    assert r.status_code == 200
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"username": "kijker", "password": "kijker-wachtwoord-123"})
        v = Session(c, r.json()["csrf_token"])
        assert (await v.get(f"/homes/{home['id']}/overview")).status_code == 200
        assert (await v.post("/homes", json={"name": "Y"})).status_code == 403
        assert (await v.post("/pairing/codes", json={})).status_code == 403


# --- pairing ----------------------------------------------------------------------


async def test_phone_pairing_flow(admin, home, app):
    import json

    import httpx

    r = await admin.post("/pairing/codes", json={"label": "Pixel"})
    assert r.status_code == 200
    payload = json.loads(r.json()["qr_payload"])
    assert payload["type"] == "shc-pair" and payload["v"] == 1 and payload["code"] == r.json()["code"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as phone:
        r2 = await phone.post(
            "/api/v1/pairing/redeem",
            json={"code": payload["code"].lower().replace("-", ""), "device_name": "Pixel"},
        )
        assert r2.status_code == 200, r2.text
        token = r2.json()["token"]
        assert r2.json()["server"]["home_name"] == "Thuis"
        h = {"Authorization": f"Bearer {token}"}
        assert (await phone.get("/api/v1/homes", headers=h)).status_code == 200
        # token auth needs no CSRF
        assert (await phone.post(f"/api/v1/alerts/{uuid.uuid4()}/ack", headers=h)).status_code == 404
        # single use
        r3 = await phone.post("/api/v1/pairing/redeem", json={"code": payload["code"], "device_name": "Again"})
        assert r3.status_code == 400
        clients = (await admin.get("/clients")).json()
        cid = next(c["id"] for c in clients if c["name"] == "Pixel")
        assert (await admin.delete(f"/clients/{cid}")).status_code == 204
        assert (await phone.get("/api/v1/homes", headers=h)).status_code == 401


# --- boiler protection ----------------------------------------------------------


async def test_boiler_socket_can_never_switch(admin, home):
    from shc_api.commanding import queue_command
    from shc_api.policy import PolicyDenied

    boiler = await add_device(home["id"], "energy_socket", "Boiler", role="boiler")
    devs = (await admin.get(f"/homes/{home['id']}/devices")).json()
    assert next(d for d in devs if d["id"] == str(boiler))["protected"] is True
    async with sessionmaker()() as db:
        dev = await db.get(Device, boiler)
        for action in ("set_relay", "set_power", "switch", "set_group", "set_hvac"):
            with pytest.raises(PolicyDenied):
                await queue_command(db, dev.home_id, dev, action, {"on": False}, "engine", "test")
    # Lights route refuses non-group devices; button mapping steps targeting the boiler are denied by the worker.
    r = await admin.post(f"/lights/groups/{boiler}", json={"on": False})
    assert r.status_code == 404
    async with sessionmaker()() as db:
        db.add(
            CommandRecord(
                home_id=uuid.UUID(home["id"]),
                device_id=boiler,
                action="set_relay",
                params={"on": False},
                source="button",
                actor="knop",
            )
        )
        await db.commit()
    from shc_api.worker import Worker

    w = Worker()
    await w.process_commands()
    async with sessionmaker()() as db:
        cmd = (await db.execute(select(CommandRecord).where(CommandRecord.device_id == boiler))).scalar_one()
        assert cmd.status == "denied" and "alleen-meten" in cmd.detail


# --- climate ----------------------------------------------------------------------


class FakeGree:
    def __init__(self):
        self.calls = []

    async def execute(self, command):
        from shc_integrations.base import CommandResult

        self.calls.append(command)
        st = {
            "power": command.params.get("power", True),
            "mode": command.params.get("mode", "heat"),
            "target_c": command.params.get("target_c"),
        }
        return CommandResult(ok=True, confirmed=True, state=st)


async def test_zone_command_policy_and_execution(admin, home):
    async with sessionmaker()() as db:
        zones = (
            (await db.execute(select(ClimateZone).where(ClimateZone.home_id == uuid.UUID(home["id"])))).scalars().all()
        )
        living = next(z for z in zones if z.name == "Woonkamer")
        bedroom = next(z for z in zones if z.name == "Slaapkamer")
    caps = {
        "caps": {
            "modes": ["off", "heat", "cool", "dry", "fan", "auto"],
            "min_c": 16,
            "max_c": 30,
            "step_c": 1,
        }
    }
    u1 = await add_device(
        home["id"], "ac_unit", "Unit 1", provider="gree", state={**caps, "mode": "heat", "target_c": 21}
    )
    u2 = await add_device(home["id"], "ac_unit", "Unit 2", provider="gree", state={**caps, "mode": "off"})
    async with sessionmaker()() as db:
        (await db.get(ClimateZone, living.id)).unit_device_id = u1
        (await db.get(ClimateZone, bedroom.id)).unit_device_id = u2
        await db.commit()
    # bedroom may not cool while living room heats (shared outdoor unit)
    r = await admin.post(f"/zones/{bedroom.id}/command", json={"mode": "cool", "target_c": 22})
    assert r.status_code == 409 and "buitenunit" in r.json()["detail"]["message"]
    z = (await admin.get(f"/zones/{bedroom.id}")).json()
    assert {"mode": "cool"}.items() <= z["blocked_modes"][0].items()
    # out of range
    r = await admin.post(f"/zones/{living.id}/command", json={"target_c": 34})
    assert r.status_code == 409
    # valid
    r = await admin.post(f"/zones/{living.id}/command", json={"target_c": 22.4, "override_minutes": 60})
    assert r.status_code == 202
    z = (await admin.get(f"/zones/{living.id}")).json()
    assert z["control"] == "manual" and z["override"]["source"] == "app"
    from shc_api.worker import Worker

    w = Worker()
    fake = FakeGree()
    async with sessionmaker()() as db:
        dev = await db.get(Device, u1)
        dev.integration_id = None
        await db.commit()
    w._adapter_for = lambda d: fake  # type: ignore[method-assign]
    await w.process_commands()
    assert fake.calls and fake.calls[0].params == {"power": True, "target_c": 22.0}
    async with sessionmaker()() as db:
        cmd = (await db.execute(select(CommandRecord).where(CommandRecord.device_id == u1))).scalars().all()[-1]
        assert cmd.status == "confirmed"
    # deliberate off stays off; resume returns to schedule
    r = await admin.post(f"/zones/{living.id}/command", json={"mode": "off"})
    assert r.status_code == 202
    await w.process_commands()
    assert fake.calls[-1].params == {"power": False}
    assert (await admin.get(f"/zones/{living.id}")).json()["control"] == "off"
    assert (await admin.post(f"/zones/{living.id}/resume")).json()["control"] == "schedule"
    # offline unit → 503
    async with sessionmaker()() as db:
        (await db.get(Device, u2)).online = False
        await db.commit()
    r = await admin.post(f"/zones/{bedroom.id}/command", json={"mode": "heat"})
    assert r.status_code == 503


async def test_schedule_roundtrip(admin, home):
    zid = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]["id"]
    body = {
        "blocks": [
            {"days": [0, 1, 2, 3, 4], "start": "07:00", "target_c": 21, "mode": "heat", "label": "Ochtend"},
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "22:30", "target_c": 18.5, "label": "Nacht"},
        ]
    }
    r = await admin.put(f"/zones/{zid}/schedule", json=body)
    assert r.status_code == 200 and len(r.json()["blocks"]) == 2
    assert (
        await admin.put(f"/zones/{zid}/schedule", json={"blocks": [{"days": [9], "start": "07:00", "target_c": 20}]})
    ).status_code == 422
    assert (await admin.get(f"/zones/{zid}")).json()["next_change"] is not None


# --- energy -----------------------------------------------------------------------


async def test_energy_summary_no_double_count_and_replacement(admin, home):
    hid = uuid.UUID(home["id"])
    grid = await add_device(home["id"], "p1_meter", "P1", role="grid")
    airco_old = await add_device(home["id"], "kwh_meter", "kWh oud", role="airco")
    day = datetime(2026, 11, 2, tzinfo=UTC)  # a Monday
    start = day - timedelta(hours=1)  # local midnight (CET = UTC+1)
    async with sessionmaker()() as db:
        for i in range(0, 97):
            t = start + timedelta(minutes=15 * i)
            for _ in range(2):  # duplicates must be ignored
                await ingest_reading(db, grid, "energy_import_kwh", 1000 + 0.25 * i, "kWh", t)
            if i <= 48:
                await ingest_reading(db, airco_old, "energy_import_kwh", 50 + 0.05 * i, "kWh", t)
        await db.commit()
    # replace airco meter at noon local: new meter starts at 0
    airco_new = await add_device(home["id"], "kwh_meter", "kWh nieuw")
    async with sessionmaker()() as db:
        role = (
            await db.execute(select(LogicalRole).where(LogicalRole.home_id == hid, LogicalRole.key == "airco"))
        ).scalar_one()
        old_b = (await db.execute(select(DeviceBinding).where(DeviceBinding.role_id == role.id))).scalar_one()
        noon = start + timedelta(hours=12)
        old_b.effective_to = noon
        db.add(DeviceBinding(role_id=role.id, device_id=airco_new, effective_from=noon))
        for i in range(0, 49):
            await ingest_reading(db, airco_new, "energy_import_kwh", 0.05 * i, "kWh", noon + timedelta(minutes=15 * i))
        await db.commit()
    r = await admin.get(f"/homes/{home['id']}/energy/summary", params={"period": "day", "date": "2026-11-02"})
    s = r.json()
    assert r.status_code == 200
    assert s["total_kwh"] == pytest.approx(24.0)
    meters = {m["role"]: m for m in s["meters"]}
    assert meters["airco"]["kwh"] == pytest.approx(4.8)
    assert meters["rest"]["quality"] == "derived" and meters["rest"]["kwh"] == pytest.approx(19.2)
    assert meters["grid"]["cost"] is None  # no tariff configured → no cost claim
    r = await admin.post(f"/homes/{home['id']}/tariffs", json={"valid_from": "2026-01-01", "import_per_kwh": 0.25})
    assert r.status_code == 200
    s = (await admin.get(f"/homes/{home['id']}/energy/summary", params={"period": "day", "date": "2026-11-02"})).json()
    assert s["cost"] == pytest.approx(6.0)
    csv = await admin.get(
        f"/homes/{home['id']}/energy/export",
        params={"format": "csv", "from": "2026-11-02", "to": "2026-11-02"},
    )
    assert csv.status_code == 200 and csv.text.startswith("tijd_utc;")
    async with sessionmaker()() as db:
        n = len((await db.execute(select(SensorReading).where(SensorReading.device_id == grid))).all())
        assert n == 97


# --- backup / restore -------------------------------------------------------------


async def test_backup_corruption_refused_and_restore_safe_mode(admin, home):
    r = await admin.post("/system/backups", json={"passphrase": "kort"})
    assert r.status_code == 422
    r = await admin.post("/system/backups", json={"passphrase": "een lange wachtwoordzin", "include_secrets": False})
    assert r.status_code == 200, r.text
    from shc_api.config import get_settings

    blob = (get_settings().backup_dir / r.json()["filename"]).read_bytes()
    with pytest.raises(bk.BackupError):
        bk.open_archive(blob, "verkeerde wachtwoordzin")
    corrupt = bytearray(blob)
    corrupt[len(corrupt) // 2] ^= 0xFF
    with pytest.raises(bk.BackupError):
        bk.open_archive(bytes(corrupt), "een lange wachtwoordzin")
    manifest, files = bk.open_archive(blob, "een lange wachtwoordzin")
    assert manifest["tables"]["homes"]["rows"] >= 1 and manifest["secrets_included"] is False
    async with sessionmaker()() as db:
        counts = await bk.restore(db, manifest, files)
        await db.commit()
    assert counts["homes"] == manifest["tables"]["homes"]["rows"]
    # sessions are not part of backups: log in again
    r = await admin.c.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
    s = Session(admin.c, r.json()["csrf_token"])
    st = (await s.get("/system/status")).json()
    assert any(c["key"] == "safe_mode" for c in st["checks"])
    zones = (await s.get(f"/homes/{home['id']}/zones")).json()
    z = next(z for z in zones if z["online"])
    r = await s.post(f"/zones/{z['id']}/command", json={"target_c": 21})
    assert r.status_code == 409 and "Veilige modus" in r.json()["detail"]["message"]
    assert (await s.post("/system/safe-mode/release")).status_code == 200
    async with sessionmaker()() as db:
        await set_setting(db, "safe_mode", False)
        await db.commit()


async def test_overview_shape(admin, home):
    r = await admin.get(f"/homes/{home['id']}/overview")
    assert r.status_code == 200
    o = r.json()
    assert {"home", "status", "zones", "lights", "energy_today", "power_now", "alerts"} <= o.keys()
    z = o["zones"][0]
    assert {"temperature", "humidity", "blocked_modes", "capabilities", "policy", "control"} <= z.keys()
    assert z["temperature"]["quality"] in ("measured", "missing")


async def test_button_mapping_conflicts():
    from shc_api.routers.lights import mapping_conflicts

    m = [
        {"button": "b1", "event": "short_release", "mode": "lights", "action": {}},
        {"button": "b1", "event": "short_release", "mode": "climate", "action": {}},
        {"button": "b2", "event": "long_press", "mode": "combined", "action": {}},
    ]
    c = mapping_conflicts(m, {"b3"})
    assert set(c) == {0, 1, 2}
    assert mapping_conflicts([{"button": "b3", "event": "x", "mode": "lights", "action": {}}], {"b3"})


async def test_zigbee_setup_and_permit_join(admin, home):
    r = await admin.post("/integrations/zigbee/permit-join", json={"home_id": home["id"]})
    assert r.status_code == 409
    r = await admin.post("/integrations/zigbee/setup", json={"home_id": home["id"]})
    assert r.status_code == 200 and r.json()["kind"] == "zigbee_bridge"
    assert (await admin.post("/integrations/zigbee/setup", json={"home_id": home["id"]})).status_code == 200
    r = await admin.post("/integrations/zigbee/permit-join", json={"home_id": home["id"], "seconds": 120})
    assert r.status_code == 202 and "open_until" in r.json()
    integ = [i for i in (await admin.get("/integrations")).json() if i["provider"] == "zigbee"]
    assert len(integ) == 1 and integ[0]["id"]


async def test_pair_persists_adapter_learned_config(admin, home, monkeypatch):
    """Regression: the worker must find the paired device by its serial, not by IP."""
    from shc_integrations.base import DiscoveredDevice, PairResult

    from shc_api.models import Integration
    from shc_api.routers import devices as devmod

    class FakeAdapter:
        def __init__(self, config, credentials):
            self.config = dict(config)

        async def pair(self, host, **kw):
            self.config.update(serial="SER123", product_type="HWE-P1", api_version="v1")
            return PairResult("SER123", {}, DiscoveredDevice("homewizard", "SER123", "p1_meter", "P1", host))

        async def close(self):
            pass

    class Info:
        adapter = FakeAdapter
        label = "HomeWizard"

    monkeypatch.setattr(devmod, "providers", lambda: {"homewizard": Info})
    r = await admin.post("/integrations/homewizard/pair", json={"home_id": home["id"], "host": "192.168.1.50"})
    assert r.status_code == 200 and r.json()["stable_identity"] == "SER123"
    async with sessionmaker()() as db:
        integ = (
            await db.execute(select(Integration).where(Integration.config["serial"].astext == "SER123"))
        ).scalar_one()
        assert integ.config["api_version"] == "v1" and integ.config["host"] == "192.168.1.50"


async def test_adaptive_and_experiment_endpoints(admin, home):
    zid = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]["id"]
    st = (await admin.get(f"/zones/{zid}/adaptive")).json()
    assert st["enabled"] is False and st["baseline"]["ready"] is False
    r = await admin.post(f"/zones/{zid}/adaptive/fit")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "insufficient_data"
    assert (await admin.post(f"/zones/{zid}/adaptive/disable")).json()["enabled"] is False
    body = {
        "name": "Nachtverlaging",
        "zone_id": zid,
        "variants": [
            {"label": "Constant", "start": "2026-10-01T00:00:00Z", "end": "2026-10-08T00:00:00Z"},
            {"label": "Nacht 18°", "start": "2026-10-08T00:00:00Z", "end": "2026-10-15T00:00:00Z"},
        ],
    }
    e = (await admin.post(f"/homes/{home['id']}/experiments", json=body)).json()
    res = (await admin.get(f"/experiments/{e['id']}")).json()
    assert res["verdict"] == "indicatief" and len(res["rows"]) == 2
    bad = {**body, "variants": [{"label": "x", "start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"}] * 2}
    assert (await admin.post(f"/homes/{home['id']}/experiments", json=bad)).status_code == 422
