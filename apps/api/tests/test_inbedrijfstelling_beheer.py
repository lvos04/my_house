"""Beheer via HTTP met fictieve apparaten en de echte werktransactie."""

import asyncio
import socket
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from asyncpg import CannotConnectNowError, ObjectNotInPrerequisiteStateError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shc_api import db as database_module
from shc_api.commissioning import lock_commissioning
from shc_api.db import sessionmaker
from shc_api.models import AuditLog, ClimateZone, CommandRecord, Device
from shc_api.store import get_setting, set_setting

from .boiler_support import protect
from .test_inbedrijfstelling_handhaving import mark, set_gate, snapshot
from .test_ws_climate_worker import make_rig
from .test_ws_security_accounts import _client, _login, _pair, _user
from .ws_devices_support import add_device, fetch, new_home

BASE = "/commissioning"


@pytest.fixture(autouse=True)
async def restore_gate(database):
    async with sessionmaker()() as db:
        prior = await get_setting(db, "commissioning_gate")
        safe = await get_setting(db, "safe_mode", False)
        await set_setting(db, "safe_mode", False)
        await db.commit()
    yield
    async with sessionmaker()() as db:
        await set_setting(db, "commissioning_gate", prior)
        await set_setting(db, "safe_mode", safe)
        await db.commit()


async def post(admin, path, body, etag=None, key=None):
    headers = {**admin.h(), "Idempotency-Key": key or str(uuid.uuid4())}
    if etag is not None:
        headers["If-Match"] = etag
    return await admin.c.post(f"/api/v1{BASE}{path}", headers=headers, json=body)


def error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == code
    assert body["message"] and isinstance(body["details"], dict)
    uuid.UUID(body["trace_id"])
    return body["details"]


async def audit_for(device):
    async with sessionmaker()() as db:
        return list((await db.scalars(
            select(AuditLog).where(
                (AuditLog.detail["device"].astext == str(device))
                | (AuditLog.detail["params"]["device_id"].astext == str(device))
            ).order_by(AuditLog.id)
        )).all())


async def test_overzicht_en_daadwerkelijk_bevestigde_bediening(admin):
    await set_gate(False)
    home_id = await new_home(admin)
    other_home = await new_home(admin)
    light = await add_device(home_id, "light", "Proeflamp")
    meter = await add_device(home_id, "p1_meter", "Proefmeter")
    archive = await add_device(home_id, "light", "Oude lamp", lifecycle="archived")
    unsupported = await add_device(home_id, "ac_unit", "Niet ondersteund", compatibility="unsupported")
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        for home, status, action, at in (
            (home_id, "confirmed", "set_light", now - timedelta(hours=1)),
            (home_id, "sent", "toggle", now),
            (other_home, "confirmed", "toggle", now),
        ):
            db.add(CommandRecord(home_id=home, device_id=light.id, status=status, action=action,
                                 params={"verborgen": True}, source="user", actor="proef", updated_at=at))
        await db.commit()
    before = await snapshot()
    state = await admin.get(BASE)
    assert state.status_code == 200 and state.headers["etag"]
    assert state.json()["task_required"] and state.json()["gate"]["enabled"] is False
    page = await admin.get(f"{BASE}/devices?home_id={home_id}&affected_only=true&limit=1")
    assert page.status_code == 200 and page.json()["total"] == 2 and page.json()["next_cursor"]
    assert page.json()["gate_etag"] == state.headers["etag"]
    second = await admin.get(
        f"{BASE}/devices?home_id={home_id}&affected_only=true&limit=1&cursor={page.json()['next_cursor']}"
    )
    assert second.json()["next_cursor"] is None
    assert {r["id"] for r in page.json()["items"] + second.json()["items"]} == {str(light.id), str(unsupported.id)}
    detail = await admin.get(f"{BASE}/devices/{light.id}")
    assert detail.json()["last_confirmed_command"] == {
        "action": "set_light", "confirmed_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }
    assert (await admin.get(f"{BASE}/devices/{meter.id}")).json()["last_confirmed_command"] is None
    error(await admin.get(f"{BASE}/devices/{uuid.uuid4()}"), 404, "not_found")
    error(await admin.get(f"{BASE}/devices?home_id={uuid.uuid4()}"), 404, "not_found")
    error(await admin.get(f"{BASE}/devices?limit=201"), 422, "validation_error")
    assert all(r["id"] != str(archive.id) for r in (await admin.get(f"{BASE}/devices?home_id={home_id}")).json()["items"])
    assert await snapshot() == before


async def test_bevestig_en_historie_en_replay(admin):
    device = await add_device(await new_home(admin), "light", "Proeflamp")
    path = f"/devices/{device.id}"
    etag = (await admin.get(BASE + path)).headers["etag"]
    key = str(uuid.uuid4())
    assert (await admin.get(BASE + path + "/history")).json()["items"] == []
    error(await post(admin, path + "/confirm", {"confirmed": True}), 428, "precondition_required")
    error(await post(admin, path + "/confirm", {"confirmed": "true"}, etag), 422, "validation_error")
    error(await post(admin, path + "/confirm", {"confirmed": False}, etag), 422, "validation_error")
    for headers, code in (({}, "idempotency_key_required"), ({"Idempotency-Key": "ongeldig"}, "idempotency_key_invalid")):
        response = await admin.c.post(f"/api/v1{BASE}{path}/confirm", json={"confirmed": True},
                                      headers={**admin.h(), "If-Match": etag, **headers})
        error(response, 400, code)
    assert (await fetch(Device, device.id)).compatibility == "untested"
    response = await post(admin, path + "/confirm", {"confirmed": True, "note": "Proefnotitie"}, etag, key)
    assert response.status_code == 200, response.text
    assert response.headers["etag"] != etag
    replay = await post(admin, path + "/confirm", {"confirmed": True, "note": "Proefnotitie"}, key=key)
    assert replay.json() == response.json() and replay.headers["idempotent-replay"] == "true"
    assert replay.headers["etag"] == response.headers["etag"]
    error(await post(admin, path + "/confirm", {"confirmed": True, "note": "Anders"}, etag, key),
          422, "idempotency_key_reused")
    details = error(await post(admin, path + "/confirm", {"confirmed": True}, etag), 412, "precondition_failed")
    assert details["huidige_etag"] == response.headers["etag"]
    rows = await audit_for(device.id)
    assert [row.action for row in rows] == ["device_marked_tested"]
    assert "note" not in rows[0].detail and "Proefnotitie" not in str(rows[0].detail)
    different = await add_device(device.home_id, "light", "Andere proeflamp")
    assert (await admin.post(f"/devices/{different.id}/tested")).status_code == 200
    async with sessionmaker()() as db:
        db.add_all([
            AuditLog(actor="historisch", action="device_marked_tested", detail={"device": str(device.id)}),
            AuditLog(actor="verkeerde woning", action="device_marked_tested",
                     detail={"device": str(device.id), "home": str(uuid.uuid4())}),
        ])
        await db.commit()
    page = (await admin.get(BASE + path + "/history?limit=1")).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    second = (await admin.get(BASE + path + f"/history?limit=1&cursor={page['next_cursor']}")).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert [page["items"][0]["status"], second["items"][0]["status"]] == ["tested", "tested"]
    assert page["items"][0]["actor"] == "historisch" and page["items"][0]["firmware"] is None
    assert all(r["actor"] and r["at"] for r in page["items"] + second["items"])
    error(await admin.get(f"{BASE}/devices/{uuid.uuid4()}/history"), 404, "not_found")
    error(await admin.get(BASE + path + "/history?cursor=0"), 422, "validation_error")


async def test_voorvertoning_volgt_indirecte_groep_en_scene(admin):
    await set_gate(False)
    home = await new_home(admin)
    light = await add_device(home, "light", "Ongeteste lamp")
    group = await add_device(home, "light_group", "Geteste groep", compatibility="tested", state={
        "members_complete": True, "member_identities": [light.stable_identity],
    })
    scene = await add_device(home, "scene", "Geteste scène", compatibility="tested",
                             state={"group_device_id": group.id.hex})
    page = await admin.get(f"{BASE}/devices?home_id={home}&affected_only=true")
    assert {row["id"] for row in page.json()["items"]} == {str(light.id), str(group.id), str(scene.id)}
    tag = (await admin.get(f"{BASE}/devices/{light.id}")).headers["etag"]
    assert (await post(admin, f"/devices/{light.id}/confirm", {"confirmed": True}, tag)).status_code == 200
    page = await admin.get(f"{BASE}/devices?home_id={home}&affected_only=true")
    assert page.json()["items"] == [] and page.json()["gate_etag"]


async def test_bestaande_leestest_behoudt_beide_auditregels(admin):
    device = await add_device(await new_home(admin), "light", "Proeflamp zonder koppeling")
    response = await admin.post(f"/devices/{device.id}/test")
    assert response.status_code == 200 and response.json()["ok"]
    after = await fetch(Device, device.id)
    assert after.compatibility == "untested" and after.tested_at is None
    assert [row.action for row in await audit_for(device.id)] == ["device_tested_probe", "request"]


async def test_bulk_helemaal_of_niets_en_bescherming(admin):
    rig = await make_rig(admin)
    home_id = rig.home_id
    first = await add_device(home_id, "light", "Eerste")
    second = await add_device(home_id, "light", "Tweede")
    unsupported = await add_device(home_id, "light", "Niet ondersteund", compatibility="unsupported")
    archive = await add_device(home_id, "light", "Archief", lifecycle="archived")
    boiler = await add_device(home_id, "energy_socket", "Afgeschermde meter")
    await protect(home_id, boiler.id)
    protected = str(boiler.id)
    await set_gate(False)
    etag = (await admin.get(BASE)).headers["etag"]
    before = await snapshot()
    for bad, status, code in (
        (uuid.uuid4(), 404, "not_found"),
        (unsupported.id, 409, "conflict"),
        (archive.id, 409, "conflict"),
        (protected, 409, "conflict"),
        (first.id, 422, "validation_error"),
    ):
        error(await post(admin, "/devices/confirm", {"confirmed": True, "device_ids": [str(first.id), str(bad)]},
                         etag), status, code)
        assert await snapshot() == before
    response = await post(admin, "/devices/confirm",
                          {"confirmed": True, "device_ids": [str(first.id), str(second.id)]}, etag)
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["items"]} == {str(first.id), str(second.id)}
    for device in (first, second):
        assert (await fetch(Device, device.id)).compatibility == "tested"
        assert [row.action for row in await audit_for(device.id)] == ["device_marked_tested"]
    single = f"/devices/{protected}"
    etag = (await admin.get(BASE + single)).headers["etag"]
    zone = rig.zones["Woonkamer"]
    async with sessionmaker()() as db:
        (await db.get(ClimateZone, zone)).unit_device_id = boiler.id  # Historische koppeling.
        await db.commit()
    await set_gate(True)
    response = await admin.post(f"/zones/{zone}/command", json={"mode": "heat"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "policy_denied"
    assert (await post(admin, single + "/confirm", {"confirmed": True}, etag)).status_code == 200
    assert (await admin.get(BASE + single)).json()["protected"] is True
    for enabled in (True, False):
        await set_gate(enabled)
        response = await admin.post(f"/zones/{zone}/command", json={"mode": "heat"})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "policy_denied"
    assert await rig.commands("Woonkamer") == []


async def test_inschakelen_herhaling_en_verouderd_gevolg(admin):
    await set_gate(False)
    home_id = await new_home(admin)
    stale = (await admin.get(BASE)).headers["etag"]
    device = await add_device(home_id, "light", "Nieuwe proeflamp")
    key = str(uuid.uuid4())
    before = await snapshot()
    error(await post(admin, "/enable", {"confirmed": True}, stale, key), 412, "precondition_failed")
    error(await post(admin, "/enable", {"confirmed": True, "enabled": False}, stale), 422, "validation_error")
    assert await snapshot() == before
    current = await admin.get(BASE)
    body = {"confirmed": True}
    response = await post(admin, "/enable", body, current.headers["etag"], key)
    assert response.status_code == 200, response.text
    assert response.json()["gate"]["enabled"] is True and response.json()["gate"]["enabled_at"]
    assert not response.json()["task_required"]
    assert (await fetch(Device, device.id)).compatibility == "untested"
    replay = await post(admin, "/enable", body, current.headers["etag"], key)
    assert replay.json() == response.json() and replay.headers["idempotent-replay"] == "true"
    again = await post(admin, "/enable", body, response.headers["etag"])
    assert again.json() == response.json()
    after = await snapshot()
    assert len(after["audit_log"]) == len(before["audit_log"]) + 1
    assert any('"commissioning_gate_enabled"' in row for row in set(after["audit_log"]) - set(before["audit_log"]))
    async with sessionmaker()() as db:
        await set_setting(db, "commissioning_gate", {"version": 99, "enabled": False})
        await db.commit()
    unknown = await admin.get(BASE)
    assert unknown.json()["gate"]["enabled"] is True and unknown.json()["gate"]["known"] is False
    error(await post(admin, "/enable", body, unknown.headers["etag"]), 409, "conflict")


async def test_beheerrechten_en_1_mib_voor_auth(app, admin):
    device = await add_device(await new_home(admin), "light", "Afgeschermde proeflamp")
    await _user(admin, "beheerproef_kijker")
    token, _ = await _pair(admin, app, "Beheerproef control")
    routes = (("GET", ""), ("GET", "/devices"), ("GET", f"/devices/{device.id}"),
              ("GET", f"/devices/{device.id}/history"), ("POST", f"/devices/{device.id}/confirm"),
              ("POST", "/devices/confirm"), ("POST", "/enable"))
    async with _client(app) as phone, _client(app) as viewer, _client(app) as anonymous:
        login = await _login(viewer, "beheerproef_kijker")
        for method, path in routes:
            payload = {"confirmed": True, **({"device_ids": [str(device.id)]} if path == "/devices/confirm" else {})}
            kwargs = {"json": payload} if method == "POST" else {}
            url = f"/api/v1{BASE}{path}"
            error(await anonymous.request(method, url, **kwargs), 401, "unauthorized")
            error(await phone.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs),
                  403, "forbidden")
            error(await viewer.request(method, url, headers=login.h(), **kwargs), 403, "forbidden")

        async def oversized():
            for _ in range(17):
                yield b"x" * 65_536

        for kwargs in ({"content": oversized()}, {"content": b"x" * 1_048_577}):
            body = error(await anonymous.post(f"/api/v1{BASE}/enable", **kwargs), 413, "payload_too_large")
            assert body["max_bytes"] == 1_048_576
        for invalid in (b"{", bytes([255])):
            content_type = {"Content-Type": "application/json"}
            error(await anonymous.post(f"/api/v1{BASE}/enable", content=invalid, headers=content_type),
                  401, "unauthorized")
            response = await admin.c.post(f"/api/v1{BASE}/enable", content=invalid,
                                          headers={**admin.h(), **content_type})
            assert error(response, 422, "validation_error")["fouten"]


def test_nieuwe_mutaties_publiceren_idempotentie_en_preconditie(app):
    from .test_ws_security_accounts import _api_routes

    schema = app.openapi()
    checked = []
    for path, route in _api_routes(app):
        if not path.startswith(f"/api/v1{BASE}/"):
            continue
        for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            operation = schema["paths"][path][method.lower()]
            headers = {p["name"].lower(): p for p in operation["parameters"] if p["in"] == "header"}
            assert headers["idempotency-key"]["required"] is True
            assert headers["if-match"]["required"] is True
            checked.append((path, method))
    assert len(checked) == 3


async def test_onbereikbare_database_geeft_contractfout(admin, app, monkeypatch):
    assert (await admin.get(BASE)).status_code == 200
    with socket.socket() as gesloten:
        gesloten.bind(("127.0.0.1", 0))
        url = database_module.engine().url.set(port=gesloten.getsockname()[1])
        unavailable = create_async_engine(url)
        try:
            with monkeypatch.context() as local:
                local.setattr(database_module, "_engine", unavailable)
                local.setattr(database_module, "_sessionmaker", async_sessionmaker(unavailable))
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(
                    transport=transport, base_url=admin.c.base_url, cookies=admin.c.cookies,
                ) as c:
                    response = await c.get(f"/api/v1{BASE}")
            assert error(response, 503, "dependency_unavailable") == {}
            assert int(response.headers["retry-after"]) > 0
        finally:
            await unavailable.dispose()


@pytest.mark.parametrize("outage", ["starting", "pool_exhausted", "missing_database", "maintenance"])
async def test_databasebeschikbaarheid_bij_sessieopenen(admin, app, monkeypatch, outage):
    assert (await admin.get(BASE)).status_code == 200
    url = database_module.engine().url

    async def starting():
        raise CannotConnectNowError("Synthetische opstartmelding")

    async def maintenance():
        raise ObjectNotInPrerequisiteStateError("De proefdatabase accepteert tijdelijk geen verbindingen")

    if outage == "starting":
        unavailable = create_async_engine(url, async_creator=starting)
    elif outage == "maintenance":
        unavailable = create_async_engine(url, async_creator=maintenance)
    elif outage == "pool_exhausted":
        unavailable = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.05)
    else:
        unavailable = create_async_engine(url.set(database=f"my_house_afwezig_{uuid.uuid4().hex}"))

    async def request():
        with monkeypatch.context() as local:
            local.setattr(database_module, "_engine", unavailable)
            local.setattr(database_module, "_sessionmaker", async_sessionmaker(unavailable))
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url=admin.c.base_url, cookies=admin.c.cookies) as c:
                return await c.get(f"/api/v1{BASE}")

    try:
        if outage == "pool_exhausted":
            async with unavailable.connect():
                response = await request()
        else:
            response = await request()
        assert error(response, 503, "dependency_unavailable") == {}
        assert int(response.headers["retry-after"]) > 0
    finally:
        await unavailable.dispose()
    assert (await admin.get(BASE)).status_code == 200


async def wait_for_lock_queue(size):
    async with asyncio.timeout(5):
        async with sessionmaker()() as db:
            while (await db.scalar(text("""
                SELECT count(*) FROM pg_locks
                WHERE locktype = 'advisory' AND NOT granted
                  AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
            """))) < size:
                await asyncio.sleep(0.01)


async def test_worker_ziet_inschakeling_voor_een_oude_opdracht(admin):
    rig = await make_rig(admin)
    await set_gate(False)
    await mark(rig, "untested")
    zone = rig.zones["Woonkamer"]
    assert (await admin.post(f"/zones/{zone}/command", json={"mode": "heat"})).status_code == 202
    etag = (await admin.get(BASE)).headers["etag"]
    async with sessionmaker()() as blocker:
        await lock_commissioning(blocker)
        enabling = asyncio.create_task(post(admin, "/enable", {"confirmed": True}, etag))
        await wait_for_lock_queue(1)
        worker = asyncio.create_task(rig.run_commands())
        await wait_for_lock_queue(2)
        await blocker.commit()
    response, _ = await asyncio.wait_for(asyncio.gather(enabling, worker), timeout=5)
    assert response.status_code == 200, response.text
    assert rig.fakes["Woonkamer"].calls == []
    assert (await rig.commands("Woonkamer"))[0].status == "denied"


async def test_worker_ziet_bevestiging_en_dubbelklik(admin):
    rig = await make_rig(admin)
    await set_gate(False)
    await mark(rig, "untested")
    device = rig.units["Woonkamer"]
    zone = rig.zones["Woonkamer"]
    assert (await admin.post(f"/zones/{zone}/command", json={"mode": "heat"})).status_code == 202
    await set_gate(True)
    path = f"/devices/{device}"
    etag = (await admin.get(BASE + path)).headers["etag"]
    key = str(uuid.uuid4())
    async with sessionmaker()() as blocker:
        await lock_commissioning(blocker)
        confirming = asyncio.create_task(post(admin, path + "/confirm", {"confirmed": True}, etag, key))
        await wait_for_lock_queue(1)
        duplicate = await asyncio.wait_for(post(admin, path + "/confirm", {"confirmed": True}, etag, key), timeout=5)
        error(duplicate, 409, "idempotency_in_progress")
        worker = asyncio.create_task(rig.run_commands())
        await wait_for_lock_queue(2)
        await blocker.commit()
    response, _ = await asyncio.wait_for(asyncio.gather(confirming, worker), timeout=5)
    assert response.status_code == 200, response.text
    assert len(rig.fakes["Woonkamer"].calls) == 1
    assert (await rig.commands("Woonkamer"))[0].status == "confirmed"
    assert [row.action for row in await audit_for(device)] == ["device_marked_tested"]
