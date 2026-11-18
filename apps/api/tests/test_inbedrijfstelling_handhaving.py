"""Eerste handhavingsdeel: directe bediening, commandokern en uitvoerhercontrole.

Alle apparaten en adapters zijn fictief. De beheer-API wordt niet vooruitgebouwd.
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from shc_api.commanding import queue_command
from shc_api.db import sessionmaker
from shc_api.models import CommandRecord, Device
from shc_api.policy import DeviceFacts, PolicyDenied, check_command
from shc_api.store import get_setting, set_setting

from .test_ws_climate_worker import make_rig


async def set_gate(enabled):
    async with sessionmaker()() as db:
        await set_setting(db, "commissioning_gate", {
            "version": 1, "enabled": enabled, "origin": "existing_devices",
            "enabled_at": None, "enabled_by": None,
        })
        await db.commit()


@pytest.fixture(autouse=True)
async def commissioning_state(database):
    async with sessionmaker()() as db:
        previous = await get_setting(db, "commissioning_gate")
        safe = await get_setting(db, "safe_mode", False)
        await set_setting(db, "safe_mode", False)
        await db.commit()
    await set_gate(True)
    yield
    async with sessionmaker()() as db:
        await set_setting(db, "commissioning_gate", previous)
        await set_setting(db, "safe_mode", safe)
        await db.commit()


async def mark(rig, status):
    async with sessionmaker()() as db:
        dev = await db.get(Device, rig.units["Woonkamer"])
        dev.compatibility = status
        dev.tested_at = datetime.now(UTC) if status == "tested" else None
        await db.commit()


async def snapshot():
    """Logische rijbytes van iedere applicatietabel, inclusief audit en wachtrij."""
    async with sessionmaker()() as db:
        tables = (await db.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ))).scalars().all()
        return {table: (await db.execute(text(
            f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY row_to_json(t)::text'  # noqa: S608 — namen uit pg_tables.
        ))).scalars().all() for table in tables}


@pytest.mark.parametrize("status", ["untested", "unsupported", ""])
@pytest.mark.parametrize("body", [{}, {"mode": "heat", "target_c": 23}])
async def test_direct_weigert_voor_validatie_en_schrijft_niets(admin, status, body):
    rig = await make_rig(admin)
    await mark(rig, status)
    before = await snapshot()
    response = await admin.post(f'/zones/{rig.zones["Woonkamer"]}/command', json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "device_not_tested", response.text
    assert "Apparaten" in response.json()["detail"]["message"]
    assert "command_id" not in response.json()
    assert await snapshot() == before
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].calls == []


@pytest.mark.parametrize("enabled,status", [(False, "untested"), (True, "tested")])
async def test_direct_positief_tot_adapter(admin, enabled, status):
    rig = await make_rig(admin)
    await set_gate(enabled)
    await mark(rig, status)
    response = await admin.post(f'/zones/{rig.zones["Woonkamer"]}/command', json={"mode": "heat"})
    assert response.status_code == 202, response.text
    await rig.run_commands()
    assert len(rig.fakes["Woonkamer"].calls) == 1
    assert (await rig.commands("Woonkamer"))[0].status == "confirmed"
    async with sessionmaker()() as db:
        assert (await db.get(Device, rig.units["Woonkamer"])).compatibility == status


@pytest.mark.parametrize("source", ["user", "button", "engine"])
async def test_commandokern_weigert_iedere_bron(admin, source):
    rig = await make_rig(admin)
    async with sessionmaker()() as db:
        dev = await db.get(Device, rig.units["Woonkamer"])
        with pytest.raises(PolicyDenied) as caught:
            await queue_command(db, rig.home_id, dev, "set_hvac", {"mode": "heat"}, source, "proef")
        assert caught.value.code == "device_not_tested"
        await db.commit()  # Zelfs een foutief committende aanroeper houdt geen wachtrij over.
    assert await rig.commands("Woonkamer") == []


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("status", ["untested", "tested"])
async def test_direct_lichtdoel_en_scene(admin, home, scene, status):
    from shc_api.worker import Worker

    from .test_api import add_device
    from .test_ws_climate_worker import FakeUnit

    group = await add_device(home["id"], "light_group", "Proefgroep")
    target = await add_device(home["id"], "scene", "Proefscène", state={"group_device_id": str(group)}) if scene else group
    async with sessionmaker()() as db:
        (await db.get(Device, group)).compatibility = "tested"
        (await db.get(Device, target)).compatibility = status
        await db.commit()
    before = await snapshot()
    body = {"scene_id": str(target)} if scene else ({"on": True} if status == "tested" else {})
    response = await admin.post(f"/lights/groups/{group}", json=body)
    if status == "untested":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "device_not_tested"
        assert await snapshot() == before
    else:
        assert response.status_code == 202, response.text
        fake, worker = FakeUnit(), Worker()
        worker._adapter_for = lambda dev: fake if dev.id == target else None
        await worker.process_commands({uuid.UUID(home["id"])})
        assert len(fake.calls) == 1


async def test_gevulde_bestaande_installatie_blijft_bedienbaar(monkeypatch):
    from alembic import command
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from shc_api import backup
    from shc_api.commissioning import read_gate
    from shc_api.worker import Worker, commands

    from .test_inbedrijfstelling_behoud import seed_and_bytes
    from .test_ws_backup_format import temp_db
    from .test_ws_climate_worker import FakeUnit

    async with temp_db("0004") as url:
        await seed_and_bytes(url, seed=True)
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE devices SET compatibility='untested', tested_at=NULL"))
            before = await seed_and_bytes(url)
            await asyncio.to_thread(command.upgrade, backup.alembic_config(url), "head")
            assert await seed_and_bytes(url) == before
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as db:
                assert (await read_gate(db))["enabled"] is False
                dev = (await db.execute(select(Device).where(Device.kind == "light"))).scalar_one()
                device_id, home_id = dev.id, dev.home_id
                cmd = await queue_command(db, home_id, dev, "set_light", {"on": True}, "user", "proef")
                command_id = cmd.id
                await db.commit()
            monkeypatch.setattr(commands, "sessionmaker", lambda: maker)
            fake, worker = FakeUnit(), Worker()
            worker._adapter_for = lambda dev: fake if dev.id == device_id else None
            await worker.process_commands({home_id})
            assert len(fake.calls) == 1
            async with maker() as db:
                assert (await db.get(CommandRecord, command_id)).status == "confirmed"
                dev = await db.get(Device, device_id)
                assert dev.compatibility == "untested" and dev.tested_at is None
        finally:
            await engine.dispose()


@pytest.mark.parametrize("source", ["user", "button", "engine"])
@pytest.mark.parametrize("retry", [False, True])
async def test_worker_oude_wachtrij_en_retry(admin, source, retry):
    rig = await make_rig(admin)
    async with sessionmaker()() as db:
        db.add(CommandRecord(home_id=rig.home_id, device_id=rig.units["Woonkamer"],
                             action="set_hvac", params={"mode": "heat"}, source=source,
                             actor="proef", attempts=int(retry)))
        await db.commit()
    await rig.run_commands()
    rows = await rig.commands("Woonkamer")
    assert len(rows) == 1 and rows[0].status == "denied"
    assert "niet getest" in rows[0].detail
    assert rig.fakes["Woonkamer"].calls == []


@pytest.mark.parametrize("change", ["ongetest", "beschermd", "gate-aan"])
async def test_worker_hercontrole_na_claim(admin, monkeypatch, change):
    from .boiler_support import protect

    rig = await make_rig(admin)
    await mark(rig, "untested" if change == "gate-aan" else "tested")
    if change == "gate-aan":
        await set_gate(False)
    response = await admin.post(f'/zones/{rig.zones["Woonkamer"]}/command', json={"mode": "heat"})
    assert response.status_code == 202, response.text
    original = rig.w._claim_next

    async def claim(home_ids):
        result = await original(home_ids)
        if result:
            if change == "beschermd":
                await protect(rig.home_id, rig.units["Woonkamer"])
            elif change == "gate-aan":
                await set_gate(True)
            else:
                await mark(rig, "untested")
        return result

    monkeypatch.setattr(rig.w, "_claim_next", claim)
    await rig.run_commands()
    assert rig.fakes["Woonkamer"].calls == []
    assert (await rig.commands("Woonkamer"))[0].status == "denied"


async def test_claim_weigert_al_voor_sending(admin):
    rig = await make_rig(admin)
    async with sessionmaker()() as db:
        db.add(CommandRecord(home_id=rig.home_id, device_id=rig.units["Woonkamer"],
                             action="set_hvac", params={"mode": "heat"}, source="user", actor="proef"))
        await db.commit()
    assert await rig.w._claim_next(rig.homes) is False
    assert (await rig.commands("Woonkamer"))[0].status == "denied"
    assert rig.fakes["Woonkamer"].calls == []


@pytest.mark.parametrize("method,path,body", [
    ("PATCH", "/devices/{id}", {"name": "Proefnaam"}),
    ("POST", "/devices/{id}/bind", {"role": "boiler"}),
    ("POST", "/devices/{id}/unbind", {"role": "boiler"}),
    ("DELETE", "/devices/{id}", None),
    ("POST", "/devices/{id}/replace", {"new_device_id": str(uuid.uuid4())}),
    ("POST", "/devices/{id}/tested", {}),
    ("POST", "/integrations/onbekend/pair", {"home_id": str(uuid.uuid4()), "host": "proef.local"}),
    ("PATCH", "/integrations/{id}", {"name": "Proefnaam"}),
    ("DELETE", "/integrations/{id}", None),
    ("POST", "/integrations/zigbee/setup", {"home_id": str(uuid.uuid4())}),
])
async def test_api_schrijfwegen_wachten_op_handhaving(admin, method, path, body):
    from shc_api.commissioning import lock_commissioning

    async with sessionmaker()() as db:
        await lock_commissioning(db)
        task = asyncio.create_task(admin.c.request(
            method, "/api/v1" + path.format(id=uuid.uuid4()), json=body, headers=admin.h(),
        ))
        try:
            done, _ = await asyncio.wait({task}, timeout=0.1)
            assert not done, "De schrijfroutelock ontbreekt; de route liep al door."
            await db.commit()
            result = await asyncio.wait_for(task, timeout=10)
            assert result.status_code == 404, result.text  # Ingang bereikt, geen echt doel of netwerk.
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_vergrendeling_blijft_tot_adapter_klaar_is(admin, monkeypatch):
    from shc_api.commissioning import lock_commissioning

    rig = await make_rig(admin)
    await mark(rig, "tested")
    assert (await admin.post(f'/zones/{rig.zones["Woonkamer"]}/command', json={"mode": "heat"})).status_code == 202
    inside, finish = asyncio.Event(), asyncio.Event()
    original = rig.fakes["Woonkamer"].execute

    async def execute(command):
        inside.set()
        await finish.wait()
        return await original(command)

    async def revoke():
        async with sessionmaker()() as db:
            await lock_commissioning(db)
            dev = await db.get(Device, rig.units["Woonkamer"])
            dev.compatibility = "untested"
            await db.commit()

    monkeypatch.setattr(rig.fakes["Woonkamer"], "execute", execute)
    worker = asyncio.create_task(rig.run_commands())
    revocation = None
    try:
        await asyncio.wait_for(inside.wait(), timeout=10)
        revocation = asyncio.create_task(revoke())
        done, _ = await asyncio.wait({revocation}, timeout=0.1)
        assert not done, "De markering kon wijzigen terwijl de adapteropdracht nog liep."
        finish.set()
        await asyncio.wait_for(asyncio.gather(worker, revocation), timeout=10)
    finally:
        finish.set()
        for task in [worker, revocation]:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert len(rig.fakes["Woonkamer"].calls) == 1


@pytest.mark.parametrize("entrance", ["feiten", "buitenunit", "inventaris", "koppeling"])
async def test_interne_ingangen_nemen_eerst_dezelfde_lock(admin, home, entrance):
    from types import SimpleNamespace

    from shc_api.commanding import lock_outdoor_group
    from shc_api.commissioning import command_facts, lock_commissioning
    from shc_api.routers.lights import normalize_mapping
    from shc_api.worker import Worker

    from .test_api import add_device

    did = await add_device(home["id"], "button", "Lockproef")

    async def enter():
        async with sessionmaker()() as db:
            dev = await db.get(Device, did)
            if entrance == "feiten":
                await command_facts(db, dev)
            elif entrance == "buitenunit":
                await lock_outdoor_group(db, SimpleNamespace(id=uuid.uuid4(), home_id=dev.home_id, outdoor_group=None))
            elif entrance == "inventaris":
                # Alleen de ingang; onbekende woning stopt vóór adapter- of eventverwerking.
                await Worker().handle_events(SimpleNamespace(home_id=uuid.uuid4()), [object()])
            else:
                await normalize_mapping(db, dev, {"button": "proef", "event": "short_release",
                                                  "mode": "lights", "action": {"steps": []}})

    async with sessionmaker()() as db:
        await lock_commissioning(db)
        task = asyncio.create_task(enter())
        try:
            done, _ = await asyncio.wait({task}, timeout=0.1)
            assert not done, "De ingang las of valideerde zonder gezamenlijke vergrendeling."
            await db.commit()
            await asyncio.wait_for(task, timeout=10)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("status", ["tested", "untested"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_bescherming_heeft_voorrang(admin, status, enabled):
    from .boiler_support import protect

    rig = await make_rig(admin)
    await mark(rig, status)
    await set_gate(enabled)
    await protect(rig.home_id, rig.units["Woonkamer"])
    before = await snapshot()
    response = await admin.post(f'/zones/{rig.zones["Woonkamer"]}/command', json={})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied"
    assert "boiler" in response.json()["detail"]["message"]
    assert await snapshot() == before


async def test_gelijktijdig_ongetest_worden_wint_voor_uitvoering(admin):
    from shc_api.commissioning import lock_commissioning

    rig = await make_rig(admin)
    await mark(rig, "tested")
    assert (await admin.post(f'/zones/{rig.zones["Woonkamer"]}/command', json={"mode": "heat"})).status_code == 202
    async with sessionmaker()() as db:
        await lock_commissioning(db)
        dev = await db.get(Device, rig.units["Woonkamer"])
        dev.compatibility = "untested"
        await db.flush()
        task = asyncio.create_task(rig.run_commands())
        try:
            done, _ = await asyncio.wait({task}, timeout=0.1)
            assert not done
            assert rig.fakes["Woonkamer"].calls == []
            await db.commit()
            await asyncio.wait_for(task, timeout=10)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert rig.fakes["Woonkamer"].calls == []
    assert (await rig.commands("Woonkamer"))[0].status == "denied"


def test_pure_policy_gate_en_beheeruitzondering():
    facts = DeviceFacts("ac_unit", frozenset(), frozenset(), compatibility="untested", commissioning_gate=True)
    with pytest.raises(PolicyDenied) as caught:
        check_command(facts, "set_hvac", {})
    assert caught.value.code == "device_not_tested"
    check_command(replace(facts, compatibility="tested"), "set_hvac", {})
    check_command(replace(facts, commissioning_gate=False), "set_hvac", {})
    check_command(replace(facts, kind="zigbee_bridge"), "permit_join", {})
    with pytest.raises(PolicyDenied):
        check_command(replace(facts, kind="zigbee_bridge"), "set_group", {})
