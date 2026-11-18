"""Volledige lokale boilerketens: echte ingangen, observeerbare weigering, nul adapteruitvoer."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from shc_integrations.base import ButtonEvent, Command, CommandResult
from shc_integrations.base import PolicyDenied as AdapterPolicyDenied
from shc_integrations.homewizard import HomeWizardAdapter
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import ButtonMapping, ClimateZone, Device, ProcessedEvent, ScheduleBlock
from shc_api.store import get_setting, ingest_reading, set_setting
from shc_api.worker import Worker

from .boiler_support import boiler_rig as boiler_rig
from .boiler_support import commands, mapping, protect
from .conftest import ADMIN_PW, Session
from .test_ws_climate_worker import make_rig, sensor_value
from .test_ws_security_ws import FakeWS
from .ws_devices_support import add_integration, fetch


class Recorder:
    def __init__(self):
        self.calls = []

    async def execute(self, command):
        self.calls.append(command)
        return CommandResult(ok=True, confirmed=True, state={"power": True, "mode": "heat", "target_c": 23})


async def _legacy_mapping(rig, step, mode="lights"):
    async with sessionmaker()() as db:
        m = ButtonMapping(home_id=rig.home, device_id=rig.button, **mapping(step, mode))
        db.add(m)
        await db.commit()
        return m.id


async def _press(rig, worker):
    integ = await add_integration(rig.home, "test")
    worker.integrations[integ.id] = integ
    button = await fetch(Device, rig.button)
    event = ButtonEvent(button.stable_identity, "testknop", "short_release", datetime.now(UTC), event_id=uuid.uuid4().hex)
    await worker.handle_events(integ, [event])
    await worker.handle_events(integ, [event])
    async with sessionmaker()() as db:
        processed = list((await db.execute(select(ProcessedEvent).where(ProcessedEvent.device_id == rig.button))).scalars())
        assert len(processed) == 1  # Bewijst een werkelijk verwerkte, vervolgens ontdubbelde druk.
        assert (await db.get(Device, rig.button)).state["last_event"]["button"] == "testknop"


@pytest.mark.parametrize("weg", ["licht", "scene", "zone", "hervatten", "gecombineerd"])
async def test_echte_knopdruk_en_replay(admin, boiler_rig, weg):
    r = boiler_rig
    if weg in ("zone", "hervatten"):
        step = {"zone_id": str(r.zone), **({"action": "resume"} if weg == "hervatten" else {"mode": "heat", "target_c": 23})}
        await _legacy_mapping(r, step, "climate")
    elif weg == "gecombineerd":
        async with sessionmaker()() as db:
            db.add(ButtonMapping(home_id=r.home, device_id=r.button, button="testknop", event="short_release",
                                 mode="combined", combined_confirmed=True, action={"steps": [
                                     {"device_id": str(r.protected_group), "action": "toggle"},
                                     {"zone_id": str(r.zone), "mode": "heat", "target_c": 23},
                                 ]}))
            await db.commit()
    else:
        await _legacy_mapping(r, {"device_id": str(r.protected_scene if weg == "scene" else r.protected_group),
                                  "action": "recall_scene" if weg == "scene" else "toggle"})
    w, adapter = Worker(), Recorder()
    w.started_at = datetime.now(UTC) - timedelta(minutes=1)
    w._adapter_for = lambda d: adapter
    if weg == "hervatten":
        # Oud schema dat al in de database stond voordat het doel werd beschermd.
        async with sessionmaker()() as db:
            db.add(ScheduleBlock(zone_id=r.zone, days=list(range(7)), start="00:00", target_c=23, mode="heat"))
            (await db.get(ClimateZone, r.zone)).control = "off"
            await db.commit()
    await _press(r, w)
    if weg == "hervatten":
        await w.climate_round({r.home})
        assert (await fetch(ClimateZone, r.zone)).last_decision.endswith("policy_denied")
    assert await commands(r.home) == []
    await w.process_commands({r.home})
    assert adapter.calls == []


@pytest.mark.parametrize("weg", ["licht", "scene", "zone", "hervatten"])
async def test_knopsimulatie_beschermde_oude_koppeling(admin, boiler_rig, weg):
    r = boiler_rig
    if weg in ("zone", "hervatten"):
        step = {"zone_id": str(r.zone), **({"action": "resume"} if weg == "hervatten" else {"mode": "heat"})}
        await _legacy_mapping(r, step, "climate")
    else:
        await _legacy_mapping(r, {"device_id": str(r.protected_scene if weg == "scene" else r.protected_group),
                                 "action": "recall_scene" if weg == "scene" else "toggle"})
    response = await admin.post(f"/buttons/{r.button}/simulate", json={"button": "testknop", "event": "short_release"})
    assert response.status_code == 200, response.text
    result = response.json()["mappings"][0]["steps"][0]
    if weg == "hervatten":
        assert result["result"] == "would_run" and result["action"] == "resume"  # Alleen configuratie, geen schakelsucces.
    else:
        assert result["result"] == "refused" and result["code"] == "policy_denied"
    assert await commands(r.home) == []


@pytest.mark.parametrize("weg", ["schema", "beleid", "hervatten", "profiel", "profielwijziging", "planner", "adaptief"])
async def test_automatisering_tot_aan_adapter(admin, home, weg):
    rig = await make_rig(admin, living=("heat", 20), sensor=True)
    zid, unit = rig.zones["Woonkamer"], rig.units["Woonkamer"]
    await sensor_value(rig, "Woonkamer", 18)
    await rig.schedule("Woonkamer", 23, "heat")
    method = "POST"
    body = None
    if weg == "beleid":
        method, path = "PUT", f"/zones/{zid}/policy"
        body = {"band_c": 0.6, "min_interval_s": 300}
    elif weg == "hervatten":
        await rig.set_zone("Woonkamer", control="off")
        path = f"/zones/{zid}/resume"
    elif weg in ("profiel", "profielwijziging", "planner"):
        created = await admin.post(f"/homes/{rig.home_id}/profiles", json={"name": "Warm", "target_c": 23, "mode": "heat"})
        assert created.status_code == 200, created.text
        pid = created.json()["id"]
        if weg == "planner":
            now = datetime.now(UTC)
            path = f"/homes/{rig.home_id}/experiments"
            body = {
                "name": "Boilerplanner", "zone_id": str(zid), "auto_apply": True, "variants": [
                    {"label": "Nu", "start": (now-timedelta(hours=1)).isoformat(),
                     "end": (now+timedelta(hours=1)).isoformat(), "profile_id": pid},
                    {"label": "Later", "start": (now+timedelta(hours=1)).isoformat(),
                     "end": (now+timedelta(hours=2)).isoformat(), "profile_id": pid},
                ],
            }
        else:
            path, body = f"/zones/{zid}/profile", {"profile_id": pid}
            if weg == "profielwijziging":
                applied = await admin.post(path, json=body)
                assert applied.status_code == 200, applied.text
                method, path = "PUT", f"/profiles/{pid}"
                body = {"name": "Warm", "target_c": 24, "mode": "heat"}
    elif weg == "adaptief":
        # Echte fit uit opgeslagen meetreeksen, geen geïnjecteerd advies of commando.
        at = datetime.now(UTC) - timedelta(hours=30)
        temp = 18.0
        async with sessionmaker()() as db:
            for i in range(110):
                sp = 20.0 if i % 16 < 8 else 23.0
                moment = at + timedelta(minutes=15*i)
                await ingest_reading(db, rig.units["sensor:Woonkamer"], "temperature", temp, "°C", moment)
                await ingest_reading(db, unit, "target_c", sp, "°C", moment)
                temp += (0.4*(sp-temp)-0.1)*0.25
            await db.commit()
        fitted = await admin.post(f"/zones/{zid}/adaptive/fit")
        assert fitted.status_code == 200 and fitted.json()["params"]["usable"], fitted.text
        path, body = f"/zones/{zid}/adaptive/activate", {"model_id": fitted.json()["id"]}
    else:
        method, path = "PUT", f"/zones/{zid}/schedule"
        body = {"blocks": [
            {"days": list(range(7)), "start": "00:00", "target_c": 23, "mode": "heat"},
        ]}
    response = await admin.c.request(method, "/api/v1" + path, json=body, headers=admin.h())
    assert response.status_code == 200, response.text
    if weg == "planner":
        await rig.w._experiments_round(now, rig.homes)
    await protect(rig.home_id, unit)
    denied = await admin.c.request(method, "/api/v1" + path, json=body, headers=admin.h())
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "policy_denied", denied.text
    await rig.round()
    zone = await rig.zone("Woonkamer")
    assert zone.last_decision.endswith("policy_denied"), zone.last_decision
    if weg == "planner":
        experiment = (await admin.get(f"/experiments/{response.json()['id']}")).json()
        assert experiment["status"] == "running" and experiment["current_variant"] == 0
        assert str(zone.profile_id) == pid
    if weg == "adaptief":
        assert zone.adaptive_enabled
        assert (await admin.post(f"/zones/{zid}/adaptive/disable")).status_code == 409
    assert await rig.commands("Woonkamer") == []
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].calls == []


@pytest.mark.parametrize("source", ["user", "button", "engine"])
@pytest.mark.parametrize("retry", [False, True])
async def test_worker_hercontrole_na_rolwijziging(admin, boiler_rig, source, retry):
    r = boiler_rig
    w, adapter = Worker(), Recorder()
    w._adapter_for = lambda d: adapter
    if source == "engine":
        rig = await make_rig(admin, living=("heat", 20))
        await rig.schedule("Woonkamer", 23, "heat")
        await rig.round()
        hid, target = rig.home_id, rig.units["Woonkamer"]
        worker = rig.w
    else:
        hid, target, worker = r.home, r.group, w
        if source == "user":
            response = await admin.post(f"/lights/groups/{r.group}", json={"on": True})
            assert response.status_code == 202, response.text
        else:
            await _legacy_mapping(r, {"device_id": str(r.group), "action": "toggle"})
            await _press(r, w)
    queued = [c for c in await commands(hid) if c.device_id == target]
    assert len(queued) == 1 and queued[0].status == "queued" and queued[0].source == source
    await protect(hid, target)
    if retry:
        async with sessionmaker()() as db:
            cmd = await db.get(type(queued[0]), queued[0].id)
            cmd.attempts = 1
            cmd.updated_at = datetime.now(UTC)-timedelta(seconds=1)
            await db.commit()
    await worker.process_commands({hid})
    denied = [c for c in await commands(hid) if c.device_id == target]
    assert len(denied) == 1 and denied[0].status == "denied"
    assert "alleen-meten" in denied[0].detail
    assert adapter.calls == []
    if source == "engine":
        assert rig.fakes["Woonkamer"].calls == []


@pytest.mark.parametrize("actor", ["web-admin", "phone-admin", "phone-control", "phone-viewer", "web-viewer"])
async def test_volledige_rechtenmatrix(admin, boiler_rig, app, actor):
    r = boiler_rig
    good = await admin.post(f"/buttons/{r.button}/mappings", json=mapping({"device_id": str(r.group), "action": "toggle"}))
    assert good.status_code == 200, good.text
    mid = good.json()["mapping"][0]["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        if actor == "web-admin":
            login = await client.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
            assert login.status_code == 200, login.text
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        elif actor.startswith("phone"):
            role = actor.split("-")[1]
            async with sessionmaker()() as db:
                await set_setting(db, "lan_url", "http://boilertest.local:8088")
                await db.commit()
            code = await admin.post("/pairing/codes", json={"role": role, **({"password": ADMIN_PW} if role == "admin" else {})})
            assert code.status_code == 200, code.text
            paired = await client.post("/api/v1/pairing/redeem", json={"code": code.json()["code"], "device_name": actor})
            assert paired.status_code == 200, paired.text
            headers = {"Authorization": f"Bearer {paired.json()['token']}"}
        else:
            username, password = uuid.uuid4().hex, uuid.uuid4().hex
            created = await admin.post("/auth/users", json={"username": username, "password": password, "role": "viewer"})
            assert created.status_code == 200, created.text
            login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200, login.text
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["via"] == ("token" if actor.startswith("phone") else "cookie")
        can_control = actor in ("web-admin", "phone-admin", "phone-control")
        can_manage = actor in ("web-admin", "phone-admin")
        bad = mapping({"device_id": str(r.boiler), "action": "toggle"}, button="andereknop")
        for method, url, body, manage in (
            ("post", f"/zones/{r.zone}/command", {"mode": "heat"}, False),
            ("post", f"/lights/groups/{r.boiler}", {"on": False}, False),
            ("post", f"/lights/groups/{r.group}", {"scene_id": str(r.boiler)}, False),
            ("patch", f"/zones/{r.zone}", {"unit_device_id": str(r.boiler)}, True),
            ("patch", f"/zones/{r.zone}", {"sensor_device_id": str(r.boiler)}, True),
            ("post", f"/buttons/{r.button}/mappings", bad, True),
            ("put", f"/buttons/{r.button}/mappings", [bad], True),
            ("patch", f"/button-mappings/{mid}", bad, True),
        ):
            response = await client.request(method, "/api/v1"+url, json=body, headers=headers)
            allowed = can_manage if manage else can_control
            assert response.status_code == (409 if allowed else 403), (actor, url, response.text)
            assert response.json()["detail"]["code"] == ("policy_denied" if allowed else "forbidden")
        assert await commands(r.home) == []
        positive = await client.post(f"/api/v1/lights/groups/{r.group}", json={"on": True}, headers=headers)
        assert positive.status_code == (202 if can_control else 403), positive.text


async def test_websocket_heeft_geen_commandodispatch(admin, boiler_rig, app):
    r = boiler_rig
    cookie = admin.c.cookies.get("shc_session")
    ws = await FakeWS(app, headers={"cookie": f"shc_session={cookie}", "origin": "http://test"}, query=f"home_id={r.home}").connect()
    try:
        hello = await ws.json()
        assert hello["type"] == "hello" and hello["data"]["can_control"] is True
        for action in ("set_relay", "set_group", "zone.command", "light.group.set"):
            await ws.send({"type": "command", "action": action, "device_id": str(r.boiler),
                           "zone_id": str(r.zone), "params": {"on": False}})
        await ws.send("ping")
        assert (await ws.next())["text"] == "pong"  # Alle eerdere berichten zijn echt verwerkt.
        assert await commands(r.home) == []
        worker, recorder = Worker(), Recorder()
        worker._adapter_for = lambda d: recorder
        await worker.process_commands({r.home})
        assert recorder.calls == []
    finally:
        await ws.close()


@pytest.mark.parametrize("ingang", ["web-v2", "web-v1", "cli-v2", "cli-v1"])
async def test_import_vrijgave_en_oude_knop_tot_adapter(admin, boiler_rig, tmp_path, monkeypatch, ingang):
    import argparse
    import json

    from shc_api import backup as bk
    from shc_api import cli
    from shc_api.config import get_settings
    from shc_api.routers import system

    from .test_ws_backup_api import _upload
    from .test_ws_backup_format import make_v1

    r = boiler_rig
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)
    # Een echte HTTP-aanvraag levert de te herstellen wachtrijregel; daarna verandert de rol.
    queued = await admin.post(f"/lights/groups/{r.group}", json={"on": True})
    assert queued.status_code == 202, queued.text
    await protect(r.home, r.group)
    mid = await _legacy_mapping(r, {"device_id": str(r.protected_group), "action": "toggle"})
    password = uuid.uuid4().hex
    made = await admin.post("/system/backups", json={"passphrase": password})
    assert made.status_code == 200, made.text
    path = get_settings().backup_dir / made.json()["filename"]
    if ingang.endswith("v1"):
        manifest, files = bk.open_archive(path.read_bytes(), password)
        tables = {name.removeprefix("tables/").removesuffix(".jsonl"):
                  [json.loads(line) for line in blob.splitlines() if line]
                  for name, blob in files.items() if name.startswith("tables/")}
        path = tmp_path / "oud.shcbak"
        path.write_bytes(make_v1(tables, manifest["schema_revision"], password))
    if ingang.startswith("web"):
        verified = await _upload(admin, path.read_bytes(), password)
        assert verified.status_code == 200 and verified.json()["restorable"], verified.text
        response = await admin.post("/system/restore/apply", json={
            "upload_id": verified.json()["upload_id"], "passphrase": password, "mode": "same_home",
        })
        assert response.status_code == 202, response.text
        for _ in range(600):
            job = (await admin.c.get(f"/api/v1/system/restore/jobs/{response.json()['job_id']}")).json()
            if job["state"] != "running":
                break
            await asyncio.sleep(0.05)
        assert job["state"] == "done", job
    else:
        # De CLI-ingang zelf, met echte verificatie, migratiecontrole en herstelkern.
        password_file = tmp_path / "wachtwoordzin"
        password_file.write_text(password)
        password_file.chmod(0o600)
        args = argparse.Namespace(file=str(path), passphrase_file=str(password_file),
                                  mode="same_home", yes=True, z2m_dir=None)
        assert await cli._restore(args, dry_run=False) == 0
    login = await admin.c.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    restored = Session(admin.c, login.json()["csrf_token"])
    async with sessionmaker()() as db:
        assert await get_setting(db, "safe_mode") is True
        assert await db.get(ButtonMapping, mid) is not None
    pending = [c for c in await commands(r.home) if str(c.id) == queued.json()["command_id"]]
    assert len(pending) == 1 and pending[0].status == "queued"
    assert (await restored.post("/system/safe-mode/release")).status_code == 200
    async with sessionmaker()() as db:
        assert await get_setting(db, "safe_mode") is False
    w, recorder = Worker(), Recorder()
    w._adapter_for = lambda d: recorder
    # Ook ná vrijgave blijven de geïmporteerde rol en oude knopkoppeling beschermd.
    await _press(r, w)
    assert len(await commands(r.home)) == 1
    await w.process_commands({r.home})
    assert (await commands(r.home))[0].status == "denied"
    assert recorder.calls == []


@pytest.mark.parametrize("weg", ["binden", "ontkoppelen", "vervangen", "archiveren", "getest"])
async def test_apparaatlevensloop_en_oude_relaiskoppeling(admin, boiler_rig, weg):
    from .test_boiler_herstel import database_bytes, denied
    from .ws_devices_support import add_device

    r = boiler_rig
    async with sessionmaker()() as db:
        (await db.get(ClimateZone, r.zone)).unit_device_id = None
        await db.commit()
    mid = await _legacy_mapping(r, {"device_id": str(r.boiler), "action": "set_relay", "params": {"on": False}})
    if weg == "binden":
        assert (await admin.post(f"/devices/{r.boiler}/unbind", json={"role": "boiler"})).status_code == 200
        before = database_bytes()
        response = await admin.post(f"/devices/{r.boiler}/bind", json={"role": "boiler"})
        denied(response)
        assert database_bytes() == before
    elif weg == "ontkoppelen":
        response = await admin.post(f"/devices/{r.boiler}/unbind", json={"role": "boiler"})
    elif weg == "vervangen":
        replacement = await add_device(r.home, "energy_socket", "Nieuwe testmeter")
        before = database_bytes()
        response = await admin.post(f"/devices/{r.boiler}/replace", json={"new_device_id": str(replacement.id)})
        denied(response)
        assert database_bytes() == before
        saved = await fetch(ButtonMapping, mid)
        assert saved.action["steps"][0]["device_id"] == str(r.boiler)
    elif weg == "archiveren":
        assert (await admin.delete(f"/devices/{r.boiler}")).status_code == 200
        response = await admin.c.patch(f"/api/v1/devices/{r.boiler}", json={"lifecycle": "active"}, headers=admin.h())
    else:
        response = await admin.post(f"/devices/{r.boiler}/tested", json={"note": "Lokale test"})
    if weg not in ("binden", "vervangen"):
        assert response.status_code == 200, response.text
    w, recorder = Worker(), Recorder()
    w._adapter_for = lambda d: recorder
    await _press(r, w)
    assert await commands(r.home) == []
    await w.process_commands({r.home})
    assert recorder.calls == []


@pytest.mark.parametrize("ingang", ["api", "worker"])
async def test_productie_homewizard_hook_blijft_dicht(admin, boiler_rig, monkeypatch, ingang):
    import respx

    from shc_api.routers import devices

    r = boiler_rig
    integ = await add_integration(r.home, "homewizard", config={
        "host": "127.0.0.1", "serial": "testmeter", "product_type": "HWE-SKT",
    })
    def factory(config, creds, **kw):
        return HomeWizardAdapter(
            config, creds, http_factory=lambda base_url, _fp: httpx.AsyncClient(base_url=base_url, trust_env=False), **kw,
        )
    with respx.mock:
        put = respx.put("http://127.0.0.1/api/v1/state").respond(json={"power_on": False})
        respx.get("http://127.0.0.1/api/v1/state").respond(json={"power_on": False})
        if ingang == "api":
            monkeypatch.setattr(devices, "providers", lambda: {"homewizard": SimpleNamespace(adapter=factory)})
            adapter = devices._adapter_for(integ)
        else:
            worker = Worker()
            async def no_task(*args, **kwargs):
                return None
            monkeypatch.setattr(worker, "_forever", no_task)
            monkeypatch.setattr(worker, "subscribe", no_task)
            assert await worker.start_integration(integ, factory)
            await asyncio.gather(*worker.integ_tasks[integ.id])
            adapter = worker.adapters[integ.id]
        assert isinstance(adapter, HomeWizardAdapter)
        try:
            for on in (True, False):
                with pytest.raises(AdapterPolicyDenied):
                    await adapter.execute(Command("testmeter", "set_relay", {"on": on}))
            assert put.call_count == 0
        finally:
            await adapter.close()
        # Positieve controle van dezelfde HTTP-testhulp: de mock moet werkelijk bereikbaar zijn.
        # Alleen dit losse, fictieve andere doel krijgt expliciet toestemming; productiehooks niet.
        other = factory({**integ.config, "serial": "ander-testdoel"}, {},
                        relay_allowed=lambda identity: identity == "ander-testdoel")
        try:
            result = await other.execute(Command("ander-testdoel", "set_relay", {"on": False}))
            assert result.confirmed and put.call_count == 1
        finally:
            await other.close()


async def test_ontdekken_koppelen_adres_diagnose_schakelen_nooit(admin, boiler_rig, monkeypatch):
    from shc_integrations.base import DiscoveredDevice, Health, HealthState, PairResult

    from shc_api.routers import devices

    r, calls = boiler_rig, []
    identity = uuid.uuid4().hex
    async with sessionmaker()() as db:
        d = await db.get(Device, r.boiler)
        d.provider, d.stable_identity = "homewizard", identity
        await db.commit()

    class ProbeAdapter:
        def __init__(self, config, credentials, **kw):
            self.config = config

        def found(self, host):
            return DiscoveredDevice("homewizard", identity, "energy_socket", "Lokale testmeter", host)

        async def discover(self, timeout_s):
            calls.append("discover")
            return [self.found("boilertest.local")]

        async def probe(self, host):
            calls.append("probe")
            return self.found(host)

        async def pair(self, host, **kw):
            calls.append("pair")
            self.config["host"] = host
            return PairResult(identity, {}, self.found(host))

        async def health(self):
            calls.append("health")
            return Health(HealthState.OK, "Lokale proef")

        async def execute(self, command):
            calls.append("execute")

        async def close(self):
            pass

    info = SimpleNamespace(adapter=ProbeAdapter, label="Testmeter", works_locally="lokaal",
                           needs_manufacturer_app=None, instructions=SimpleNamespace(steps=[]))
    monkeypatch.setattr(devices, "providers", lambda: {"homewizard": info})
    found = await admin.post("/integrations/homewizard/discover", json={"home_id": str(r.home), "timeout_s": 1})
    assert found.status_code == 200 and found.json()[0]["stable_identity"] == identity, found.text
    assert (await fetch(Device, r.boiler)).compatibility == "untested"
    paired = await admin.post("/integrations/homewizard/pair", json={"home_id": str(r.home), "host": "boilertest.local"})
    assert paired.status_code == 200, paired.text
    d = await fetch(Device, r.boiler)
    assert d.integration_id is not None
    assert d.compatibility == "untested"
    changed = await admin.c.patch(f"/api/v1/integrations/{d.integration_id}",
                                 json={"host": "andere-boilertest.local"}, headers=admin.h())
    assert changed.status_code == 200, changed.text
    tested = await admin.post(f"/devices/{r.boiler}/test")
    assert tested.status_code == 200 and tested.json()["ok"], tested.text
    assert (await fetch(Device, r.boiler)).compatibility == "untested"
    diagnosis = await admin.get(f"/devices/{r.boiler}/diagnostics")
    assert diagnosis.status_code == 200, diagnosis.text
    assert (await admin.post(f"/devices/{r.boiler}/tested")).status_code == 200
    assert calls.count("discover") == 1 and calls.count("pair") == 1 and calls.count("probe") == 1
    assert calls.count("health") == 1
    assert "execute" not in calls, "Leesroutes mogen execute niet aanroepen"
    assert await commands(r.home) == []


@pytest.mark.parametrize("weg", ["pauzeren", "hervatten", "stoppen", "verwijderen"])
async def test_planner_overgangen_met_profielherstel(admin, home, weg):
    rig = await make_rig(admin, living=("heat", 20), sensor=True)
    zid = rig.zones["Woonkamer"]
    await sensor_value(rig, "Woonkamer", 18)
    profiles = []
    for name, target in (("Basis", 23), ("Variant", 24)):
        response = await admin.post(f"/homes/{rig.home_id}/profiles", json={"name": name, "target_c": target, "mode": "heat"})
        assert response.status_code == 200, response.text
        profiles.append(response.json()["id"])
    assert (await admin.post(f"/zones/{zid}/profile", json={"profile_id": profiles[0]})).status_code == 200
    now = datetime.now(UTC)
    created = await admin.post(f"/homes/{rig.home_id}/experiments", json={
        "name": "Overgang", "zone_id": str(zid), "auto_apply": True, "variants": [
            {"label": "Nu", "start": (now-timedelta(hours=1)).isoformat(),
             "end": (now+timedelta(hours=1)).isoformat(), "profile_id": profiles[1]},
            {"label": "Later", "start": (now+timedelta(hours=1)).isoformat(),
             "end": (now+timedelta(hours=2)).isoformat(), "profile_id": profiles[1]},
        ],
    })
    assert created.status_code == 200, created.text
    eid = created.json()["id"]
    await rig.w._experiments_round(now, rig.homes)
    assert str((await rig.zone("Woonkamer")).profile_id) == profiles[1]
    if weg in ("pauzeren", "hervatten"):
        paused = await admin.post(f"/experiments/{eid}/pause")
        assert paused.status_code == 200 and paused.json()["status"] == "paused", paused.text
        if weg == "hervatten":
            response = await admin.post(f"/experiments/{eid}/resume")
            assert response.status_code == 200 and response.json()["status"] == "running", response.text
    elif weg == "stoppen":
        response = await admin.post(f"/experiments/{eid}/stop")
        assert response.status_code == 200 and response.json()["zone_restored"] == "restored", response.text
    else:
        assert (await admin.delete(f"/experiments/{eid}")).status_code == 204
    if weg in ("stoppen", "verwijderen"):
        assert str((await rig.zone("Woonkamer")).profile_id) == profiles[0]
    await protect(rig.home_id, rig.units["Woonkamer"])
    await rig.round()
    assert (await rig.zone("Woonkamer")).last_decision.endswith("policy_denied")
    assert await rig.commands("Woonkamer") == []
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].calls == []


@pytest.mark.parametrize("source", ["user", "button", "engine"])
@pytest.mark.parametrize("action", ["set_relay", "set_power", "switch", "set_group", "set_hvac", "recall_scene"])
async def test_interne_commandokern_weigert_elke_bron_en_actie(admin, boiler_rig, source, action):
    from shc_api.commanding import queue_command
    from shc_api.policy import PolicyDenied

    r = boiler_rig
    async with sessionmaker()() as db:
        device = await db.get(Device, r.boiler)
        with pytest.raises(PolicyDenied):
            await queue_command(db, r.home, device, action, {"on": False}, source, "Lokale proef")
        await db.commit()
    assert await commands(r.home) == []


@pytest.mark.parametrize("source", ["user", "button", "engine"])
@pytest.mark.parametrize("action", ["set_relay", "set_power", "switch", "set_group", "set_hvac", "recall_scene"])
async def test_worker_actiematrix_via_interne_commandokern(admin, boiler_rig, source, action):
    from shc_api.commanding import queue_command

    r, recorder = boiler_rig, Recorder()
    async with sessionmaker()() as db:
        unit = await db.get(Device, r.unit)
        cmd = await queue_command(db, r.home, unit, action, {"on": True, "mode": "heat"}, source, "Lokale proef")
        cid = cmd.id
        await db.commit()
    assert len(await commands(r.home)) == 1
    await protect(r.home, r.unit)
    worker = Worker()
    worker._adapter_for = lambda d: recorder
    await worker.process_commands({r.home})
    saved = next(c for c in await commands(r.home) if c.id == cid)
    assert saved.status == "denied" and "alleen-meten" in saved.detail
    assert recorder.calls == []
