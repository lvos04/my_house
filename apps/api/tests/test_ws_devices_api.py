"""Device, integration, lights/buttons and preflight APIs (verbeterlijst 8, 15, 24, 25, 27, 29, 43 and
small items "Te ruime rechten" and "De rolkeuze op Apparaten")."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import func, select, update

from shc_api.db import sessionmaker
from shc_api.models import ButtonMapping, ClimateZone, CommandRecord, Device, DeviceBinding, Integration

from .ws_devices_support import add_device, add_integration, fetch, hue_id, new_home

BRIDGE = "ecb5fafffe000001"


def _info(adapter_cls, label="Test"):
    return SimpleNamespace(
        adapter=adapter_cls,
        label=label,
        works_locally="lokaal",
        needs_manufacturer_app=None,
        instructions=SimpleNamespace(steps=[]),
    )


def _use(monkeypatch, **providers):
    from shc_api.routers import devices as devmod

    monkeypatch.setattr(devmod, "providers", lambda: providers)


async def _patch(admin, url, body):
    return await admin.c.patch("/api/v1" + url, json=body, headers=admin.h())


# --- item 24: re-pairing updates the existing integration -----------------------------------------


async def test_repair_reuses_integration_and_moves_devices(admin, monkeypatch):
    from shc_integrations.base import DiscoveredDevice, PairResult

    class FakeHue:
        def __init__(self, config, credentials, **kw):
            self.config = dict(config)

        async def pair(self, host, **kw):
            self.config["host"] = host
            dev = DiscoveredDevice("hue", BRIDGE, "hue_bridge", "Hue Bridge", host, model="BSB002")
            return PairResult(BRIDGE, {"application_key": "k", "bridge_id": BRIDGE}, dev)

        async def close(self):
            pass

    _use(monkeypatch, hue=_info(FakeHue, "Philips Hue"))
    home_id = await new_home(admin)
    old_a = await add_integration(home_id, "hue", "Hue oud", config={"host": "192.168.1.2"})
    old_b = await add_integration(home_id, "hue", "Hue dubbel", config={"host": "192.168.1.2"}, identity=BRIDGE)
    bridge_dev = await add_device(home_id, "hue_bridge", "Hue Bridge", provider="hue", stable_identity=BRIDGE)
    lamp = await add_device(home_id, "light", "Plafond", provider="hue", integration_id=old_a.id)
    async with sessionmaker()() as db:
        await db.execute(update(Device).where(Device.id == bridge_dev.id).values(integration_id=old_a.id))
        await db.commit()

    r = await admin.post("/integrations/hue/pair", json={"home_id": str(home_id), "host": "192.168.1.20"})
    assert r.status_code == 200, r.text
    assert r.json()["repaired"] is True and r.json()["integration_id"] == str(old_b.id)
    kept = await fetch(Integration, old_b.id)
    assert kept.enabled and kept.config["host"] == "192.168.1.20" and kept.identity == BRIDGE
    gone = await fetch(Integration, old_a.id)
    assert not gone.enabled and gone.credentials_enc is None and gone.identity is None
    assert (await fetch(Device, lamp.id)).integration_id == old_b.id
    assert (await fetch(Device, bridge_dev.id)).integration_id == old_b.id
    # Pairing again changes nothing in the number of integrations.
    r = await admin.post("/integrations/hue/pair", json={"home_id": str(home_id), "host": "192.168.1.20"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        n = (
            await db.execute(
                select(func.count())
                .select_from(Integration)
                .where(Integration.home_id == home_id, Integration.provider == "hue", Integration.enabled.is_(True))
            )
        ).scalar_one()
    assert n == 1
    listed = (await admin.get(f"/integrations?home_id={home_id}")).json()
    mine = [i for i in listed if i["id"] == str(old_b.id)][0]
    assert mine["host"] == "192.168.1.20" and mine["identity"] == BRIDGE and mine["enabled"] is True


async def test_pair_failure_keeps_diagnostics_and_maps_errors(admin, monkeypatch):
    from shc_integrations.base import Unsupported

    diag = {
        "stage": "bind_failed",
        "host": "192.168.1.70",
        "mac": "aabbcc112233",
        "scan": {"mac": "aabbcc112233", "model": "GWH09", "ver": "V1.2.1", "name": "Gree"},
        "bind_attempts": [
            {"encryption": "v1", "result": "geen antwoord"},
            {"encryption": "v2", "result": "geen antwoord"},
        ],
    }

    class FakeGree:
        def __init__(self, config, credentials, **kw):
            self.config = dict(config)

        async def pair(self, host, **kw):
            if host == "192.168.1.71":
                raise OSError("[Errno 111] Connection refused by 192.168.1.71")
            raise Unsupported("Binden mislukt met de oude én de nieuwe versleuteling.", diagnostics=diag)

        async def close(self):
            pass

    _use(monkeypatch, gree=_info(FakeGree, "Gree airco"))
    home_id = await new_home(admin)
    r = await admin.post("/integrations/gree/pair", json={"home_id": str(home_id), "host": "192.168.1.70"})
    assert r.status_code == 502
    body = r.json()["detail"]
    assert body["code"] == "unsupported" and body["message"].startswith("Koppelen mislukt: Binden mislukt")
    assert body["diagnostics"]["bind_attempts"][1]["encryption"] == "v2"
    integ = await fetch(Integration, uuid.UUID(body["integration_id"]))
    assert integ.enabled is False and integ.state == "error" and integ.identity == "aabbcc112233"
    assert integ.diagnostics["pair"]["diagnostics"]["scan"]["model"] == "GWH09"
    dev = await fetch(Device, uuid.UUID(body["device_id"]))
    assert dev.compatibility == "unsupported" and dev.kind == "ac_unit" and dev.model == "GWH09"
    d = (await admin.get(f"/devices/{dev.id}/diagnostics")).json()
    assert d["diagnostics"]["device"]["diagnostics"]["pair"]["code"] == "unsupported"
    assert "bind_attempts" in d["text"] and "credentials" not in d["text"]
    # A second failed attempt updates the same record.
    r = await admin.post("/integrations/gree/pair", json={"home_id": str(home_id), "host": "192.168.1.70"})
    assert r.json()["detail"]["integration_id"] == body["integration_id"]
    # Raw exception text is never echoed.
    r = await admin.post("/integrations/gree/pair", json={"home_id": str(home_id), "host": "192.168.1.71"})
    assert r.status_code == 502
    msg = r.json()["detail"]["message"]
    assert "Errno" not in msg and "Geen verbinding" in msg


async def test_pair_host_validation(admin, monkeypatch, home):
    for host in ("127.0.0.1", "169.254.10.1", "localhost", "db", "mosquitto", "8.8.8.8", "::1", "0.0.0.0", "a b"):  # noqa: S104
        r = await admin.post("/integrations/hue/pair", json={"home_id": home["id"], "host": host})
        assert r.status_code == 422, host
        assert "Traceback" not in r.text
    from shc_api.routers.devices import validate_host

    assert validate_host("192.168.1.5") == "192.168.1.5"
    assert validate_host("Hue-Bridge.local") == "hue-bridge.local"
    assert validate_host("meter.home.arpa") == "meter.home.arpa"
    monkeypatch.setenv("SHC_PAIRING_ALLOWED_HOSTS", "mosquitto")
    assert validate_host("mosquitto") == "mosquitto"


async def test_patch_integration_address_checks_identity(admin, monkeypatch):
    from shc_integrations.base import DiscoveredDevice

    answers = {"192.168.1.40": "SER-OTHER", "192.168.1.41": "SER1"}

    class FakeHW:
        def __init__(self, config, credentials, **kw):
            self.config = dict(config)

        async def probe(self, host):
            if host not in answers:
                raise TimeoutError
            return DiscoveredDevice("homewizard", answers[host], "p1_meter", "P1", host)

        async def close(self):
            pass

    _use(monkeypatch, homewizard=_info(FakeHW, "HomeWizard"))
    from shc_api import events

    published = []
    real_publish = events.publish

    async def spy(db, type_, home_id, data):
        published.append((type_, data))
        await real_publish(db, type_, home_id, data)

    monkeypatch.setattr(events, "publish", spy)
    home_id = await new_home(admin)
    integ = await add_integration(
        home_id, "homewizard", "P1", config={"host": "192.168.1.30", "serial": "SER1"}, identity="SER1"
    )
    r = await _patch(admin, f"/integrations/{integ.id}", {"host": "192.168.1.40"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "identity_mismatch"
    assert (await fetch(Integration, integ.id)).config["host"] == "192.168.1.30"
    r = await _patch(admin, f"/integrations/{integ.id}", {"host": "192.168.1.99"})
    assert r.status_code == 502 and "niet gewijzigd" in r.json()["detail"]["message"]
    assert not [p for p in published if p[0] == "integration.changed"]
    r = await _patch(admin, f"/integrations/{integ.id}", {"host": "192.168.1.41"})
    assert r.status_code == 200 and r.json()["host"] == "192.168.1.41"
    assert ("integration.changed", {"id": integ.id}) in published  # wakes the worker's reload loop
    assert (await fetch(Integration, integ.id)).config == {"host": "192.168.1.41", "serial": "SER1"}
    r = await _patch(admin, f"/integrations/{integ.id}", {"host": "192.168.1.99", "verify": False})
    assert r.status_code == 200 and r.json()["host"] == "192.168.1.99"
    assert (await _patch(admin, f"/integrations/{integ.id}", {"host": "127.0.0.1"})).status_code == 422
    zb = await add_integration(home_id, "zigbee", config={"host": "mqtt"})
    assert (await _patch(admin, f"/integrations/{zb.id}", {"host": "192.168.1.50"})).status_code == 409


# --- roles (small item "De rolkeuze op Apparaten") ----------------------------------------------------


async def test_bind_only_fitting_roles_and_unbind(admin):
    home_id = await new_home(admin)
    p1 = await add_device(home_id, "p1_meter", "P1")
    sock = await add_device(home_id, "energy_socket", "Stekker")
    r = await admin.post(f"/devices/{p1.id}/bind", json={"role": "boiler"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "role_mismatch"
    assert "Energy Socket" in r.json()["detail"]["message"]
    r = await admin.post(f"/devices/{p1.id}/bind", json={"role": "grid"})
    assert r.status_code == 200 and r.json()["roles"] == ["grid"] and r.json()["allowed_roles"] == ["grid"]
    r = await admin.post(f"/devices/{sock.id}/bind", json={"role": "boiler"})
    assert r.status_code == 200 and r.json()["protected"] is True
    assert set(r.json()["allowed_roles"]) == {"airco", "boiler"}
    r = await admin.post(f"/devices/{sock.id}/unbind", json={"role": "boiler"})
    assert r.status_code == 200 and r.json()["roles"] == [] and r.json()["protected"] is False
    assert (await admin.post(f"/devices/{sock.id}/unbind", json={"role": "boiler"})).status_code == 409
    async with sessionmaker()() as db:  # history kept: the binding is closed, not deleted
        rows = (await db.execute(select(DeviceBinding).where(DeviceBinding.device_id == sock.id))).scalars().all()
        assert len(rows) == 1 and rows[0].effective_to is not None


# --- item 43: archive, replace, test, tested, diagnostics ------------------------------------------


async def test_archive_restore_and_replace(admin):
    home_id = await new_home(admin)
    s1 = await add_device(home_id, "zigbee_sensor", "Sensor oud", provider="zigbee")
    s2 = await add_device(home_id, "zigbee_sensor", "Sensor nieuw", provider="zigbee")
    p1 = await add_device(home_id, "p1_meter", "P1")
    zones = {z["name"]: z["id"] for z in (await admin.get(f"/homes/{home_id}/zones")).json()}
    r = await _patch(admin, f"/zones/{zones['Woonkamer']}", {"sensor_device_id": str(s1.id)})
    assert r.status_code == 200
    assert (await admin.post(f"/devices/{s1.id}/bind", json={"role": "outdoor_temp"})).status_code == 200

    r = await admin.post(f"/devices/{s1.id}/replace", json={"new_device_id": str(p1.id)})
    assert r.status_code == 409  # a P1 meter can not be the outdoor/zone sensor
    r = await admin.post(f"/devices/{s1.id}/replace", json={"new_device_id": str(s2.id)})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["moved"] == {"roles": ["outdoor_temp"], "zones": 1, "mappings": 0}
    assert out["old"]["lifecycle"] == "replaced" and out["new"]["roles"] == ["outdoor_temp"]
    assert (await fetch(ClimateZone, uuid.UUID(zones["Woonkamer"]))).sensor_device_id == s2.id

    r = await admin.delete(f"/devices/{s2.id}")
    assert r.status_code == 200 and r.json()["lifecycle"] == "archived" and r.json()["roles"] == []
    assert (await fetch(ClimateZone, uuid.UUID(zones["Woonkamer"]))).sensor_device_id is None
    listed = {d["id"] for d in (await admin.get(f"/homes/{home_id}/devices")).json()}
    assert str(s2.id) not in listed
    listed = {d["id"] for d in (await admin.get(f"/homes/{home_id}/devices?include_archived=true")).json()}
    assert str(s2.id) in listed
    r = await _patch(admin, f"/devices/{s2.id}", {"lifecycle": "active"})
    assert r.status_code == 200 and r.json()["lifecycle"] == "active"
    async with sessionmaker()() as db:  # bindings are history, not deleted
        n = (
            await db.execute(select(func.count()).select_from(DeviceBinding).where(DeviceBinding.device_id == s1.id))
        ).scalar_one()
        assert n == 1


async def test_device_test_probe_tested_and_diagnostics(admin, monkeypatch):
    from shc_integrations.base import Health, HealthState

    executed = []

    class FakeHue:
        def __init__(self, config, credentials, **kw):
            self.credentials = credentials

        async def health(self):
            return Health(HealthState.OK, "Verbonden", extra={"bridge_id": BRIDGE})

        async def execute(self, command):
            executed.append(command)

        async def close(self):
            pass

    _use(monkeypatch, hue=_info(FakeHue, "Philips Hue"))
    home_id = await new_home(admin)
    integ = await add_integration(
        home_id, "hue", "Hue", config={"host": "192.168.1.2"}, creds={"application_key": "geheim"}
    )
    lamp = await add_device(home_id, "light", "Plafond", provider="hue", integration_id=integ.id, state={"on": True})
    r = await admin.post(f"/devices/{lamp.id}/test")
    assert r.status_code == 200, r.text
    res = r.json()
    checks = {c["key"]: c for c in res["checks"]}
    assert res["ok"] is True and checks["connection"]["state"] == "ok" and checks["seen"]["state"] == "ok"
    assert executed == [] and "niets geschakeld" in res["note"]

    broken = await add_integration(
        home_id, "hue", "Hue kapot", config={"host": "192.168.1.3"}, raw_credentials=b"\x01zzz"
    )
    lamp2 = await add_device(home_id, "light", "Lamp 2", provider="hue", integration_id=broken.id)
    res = (await admin.post(f"/devices/{lamp2.id}/test")).json()
    conn = next(c for c in res["checks"] if c["key"] == "connection")
    assert res["ok"] is False and conn["code"] == "key_mismatch" and "Sleutel past niet" in conn["detail"]

    r = await admin.post(f"/devices/{lamp.id}/tested", json={"note": "Werkt met de Hue-app uit"})
    assert r.status_code == 200 and r.json()["compatibility"] == "tested" and r.json()["tested_at"]
    d = (await admin.get(f"/devices/{lamp.id}/diagnostics")).json()
    assert d["diagnostics"]["device"]["diagnostics"]["tested"]["note"] == "Werkt met de Hue-app uit"
    assert d["diagnostics"]["integration"]["config"] == {"host": "192.168.1.2"}
    assert "geheim" not in d["text"] and "Plafond" in d["text"]


async def test_compatibility_matrix_as_data(admin):
    home_id = await new_home(admin)
    p1 = await add_device(home_id, "p1_meter", "P1", provider="homewizard", model="HWE-P1")
    assert (await admin.post(f"/devices/{p1.id}/tested")).status_code == 200
    m = (await admin.get("/integrations/compatibility")).json()
    assert {p["provider"] for p in m["providers"]} >= {"hue", "gree", "homewizard", "zigbee"}
    row = next(r for r in m["rows"] if r["model"].startswith("P1-meter"))
    assert row["status"] == "tested" and any(d["id"] == str(p1.id) for d in row["devices"])
    v1 = next(r for r in m["rows"] if r["model"].startswith("Bridge v1"))
    assert v1["status"] == "unsupported"


async def test_calibration_only_for_sensors(admin):
    home_id = await new_home(admin)
    p1 = await add_device(home_id, "p1_meter", "P1")
    r = await _patch(admin, f"/devices/{p1.id}/calibration", {"temperature_offset": 1})
    assert r.status_code == 409
    s = await add_device(home_id, "zigbee_sensor", "Sensor", provider="zigbee")
    assert (await _patch(admin, f"/devices/{s.id}/calibration", {"temperature_offset": 11})).status_code == 422
    r = await _patch(admin, f"/devices/{s.id}/calibration", {"humidity_offset": -3.25})
    assert r.status_code == 200 and r.json()["calibration"] == {"temperature_offset": 0.0, "humidity_offset": -3.25}


# --- preflight -----------------------------------------------------------------------------------


async def test_preflight_checks(admin, monkeypatch):
    from shc_api.routers import homes

    state, detail = await homes.discovery_probe(wait_s=0.05)
    assert state in ("ok", "warn") and detail

    async def no_discovery(wait_s=1.5):
        return "warn", "Automatisch zoeken is niet beschikbaar in het containernetwerk."

    monkeypatch.setattr(homes, "discovery_probe", no_discovery)
    r = await admin.get("/system/preflight")
    assert r.status_code == 200, r.text
    checks = {c["key"]: c for c in r.json()["checks"]}
    assert {"home", "worker", "discovery", "clock", "lan_url", "backup"} <= set(checks)
    assert "containernetwerk" in checks["discovery"]["detail"]
    assert ("mqtt" in checks and "zigbee_dongle" in checks) or "zigbee" in checks
    assert isinstance(r.json()["ready"], bool)


# --- item 25: button mappings ------------------------------------------------------------------------


async def test_button_mappings_validation_crud_and_simulate(admin):
    home_id = await new_home(admin)
    b1, b2, b3 = hue_id(1, "1"), hue_id(2, "1"), hue_id(3, "1")
    assert len(b1) == 36
    btn = await add_device(
        home_id,
        "button",
        "Dimmer hal",
        provider="hue",
        stable_identity=hue_id(1, "d"),
        state={
            "buttons": [
                {"id": b1, "control_id": 1, "type": "button", "events": ["short_release", "long_press"]},
                {"id": b2, "control_id": 2, "type": "button", "events": ["short_release"]},
                {"id": b3, "control_id": 3, "type": "button", "events": ["short_release"]},
            ],
            "bridge_rules": [b3],
        },
    )
    group = await add_device(home_id, "light_group", "Hal", provider="hue", state={"on": True})
    unit = await add_device(
        home_id,
        "ac_unit",
        "Airco",
        provider="gree",
        state={"caps": {"modes": ["off", "heat", "cool"], "step_c": 1.0}, "power": False, "mode": "off"},
    )
    zones = {z["name"]: z["id"] for z in (await admin.get(f"/homes/{home_id}/zones")).json()}
    await _patch(admin, f"/zones/{zones['Woonkamer']}", {"unit_device_id": str(unit.id)})
    light_step = {"steps": [{"device_id": str(group.id), "action": "toggle"}]}
    zone_step = {"steps": [{"zone_id": zones["Woonkamer"], "target_c": 22}]}

    async def add(body):
        return await admin.post(f"/buttons/{btn.id}/mappings", json=body)

    r = await add({"button": "x" * 65, "event": "short_release", "mode": "lights", "action": light_step})
    assert r.status_code == 422
    r = await add({"button": b1, "event": "short_release", "mode": "lights", "action": zone_step})
    assert r.status_code == 422 and "lichtkoppeling" in r.json()["detail"]["message"]
    r = await add({"button": b1, "event": "short_release", "mode": "climate", "action": light_step})
    assert r.status_code == 422 and "klimaatkoppeling" in r.json()["detail"]["message"]
    r = await add({"button": hue_id(9, "1"), "event": "short_release", "mode": "lights", "action": light_step})
    assert r.status_code == 422 and "hoort niet bij" in r.json()["detail"]["message"]
    r = await add({"button": b2, "event": "long_press", "mode": "lights", "action": light_step})
    assert r.status_code == 422  # b2 has no long_press

    r = await add({"button": b1, "event": "short_release", "mode": "lights", "action": light_step})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["mapping"][0]["action"] == {
        "steps": [{"type": "light", "device_id": str(group.id), "action": "toggle"}]
    }
    first_id = view["mapping"][0]["id"]
    r = await add({"button": b1, "event": "short_release", "mode": "lights", "action": light_step})
    assert r.status_code == 409
    r = await _patch(admin, f"/button-mappings/{first_id}", {"enabled": False})
    assert r.status_code == 200 and r.json()["mapping"][0]["enabled"] is False
    r = await add({"button": b1, "event": "short_release", "mode": "lights", "action": light_step})
    assert r.status_code == 200  # a disabled mapping does not conflict
    assert (await admin.delete(f"/button-mappings/{first_id}")).status_code == 204

    r = await add({"button": b2, "event": "short_release", "mode": "climate", "action": zone_step})
    assert r.status_code == 200
    r = await add({"button": b3, "event": "short_release", "mode": "lights", "action": light_step})
    assert r.status_code == 200
    b3_map = next(m for m in r.json()["mapping"] if m["button"] == b3)
    assert "Hue Bridge" in b3_map["conflict"]
    buttons = {b["id"]: b for b in r.json()["buttons"]}
    assert buttons[b3]["bridge_rule"] is True and buttons[b1]["bridge_rule"] is False

    async with sessionmaker()() as db:
        before = (await db.execute(select(func.count()).select_from(CommandRecord))).scalar_one()
    sim = (await admin.post(f"/buttons/{btn.id}/simulate", json={"button": b1, "event": "short_release"})).json()
    step = sim["mappings"][0]["steps"][0]
    assert sim["would_run"] is True and step["result"] == "would_run"
    assert step["action"] == "set_group" and step["params"] == {"on": False}  # toggle of a group that is on
    sim = (await admin.post(f"/buttons/{btn.id}/simulate", json={"button": b2, "event": "short_release"})).json()
    step = sim["mappings"][0]["steps"][0]
    assert sim["would_run"] is False and step["result"] == "refused" and "Kies eerst een modus" in step["reason"]
    sim = (await admin.post(f"/buttons/{btn.id}/simulate", json={"button": b3, "event": "short_release"})).json()
    assert sim["bridge_rule"] is True and "Hue Bridge" in sim["message"]
    sim = (await admin.post(f"/buttons/{btn.id}/simulate", json={"button": b2, "event": "long_press"})).json()
    assert sim["mappings"] == [] and "niets gekoppeld" in sim["message"]
    async with sessionmaker()() as db:
        after = (await db.execute(select(func.count()).select_from(CommandRecord))).scalar_one()
        assert after == before, "simulate never queues anything"
        z = await db.get(ClimateZone, uuid.UUID(zones["Woonkamer"]))
        assert z.control == "schedule"

    # PUT (bulk) uses the same validation and conflict rules.
    r = await admin.put(
        f"/buttons/{btn.id}/mappings",
        json=[
            {
                "button": b1,
                "event": "short_release",
                "mode": "combined",
                "action": {"steps": light_step["steps"] + zone_step["steps"]},
            }
        ],
    )
    assert r.status_code == 409  # combined must be confirmed
    r = await admin.put(
        f"/buttons/{btn.id}/mappings",
        json=[
            {
                "button": b1,
                "event": "short_release",
                "mode": "combined",
                "combined_confirmed": True,
                "action": {"steps": light_step["steps"] + zone_step["steps"]},
            }
        ],
    )
    assert r.status_code == 200 and len(r.json()["mapping"]) == 1
    listed = (await admin.get(f"/homes/{home_id}/buttons")).json()
    assert listed[0]["id"] == str(btn.id) and len(listed[0]["buttons"]) == 3
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(func.count()).select_from(ButtonMapping).where(ButtonMapping.device_id == btn.id))
        ).scalar_one() == 1


async def test_archived_group_is_not_commandable(admin):
    home_id = await new_home(admin)
    g = await add_device(home_id, "light_group", "Weg", provider="hue", lifecycle="archived")
    assert (await admin.post(f"/lights/groups/{g.id}", json={"on": True})).status_code == 404
    assert str(g.id) not in {x["id"] for x in (await admin.get(f"/homes/{home_id}/lights")).json()["groups"]}
    _ = datetime.now(UTC)
