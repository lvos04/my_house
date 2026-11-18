"""Gate aan na herstel via web en CLI, tot vrijgave en uitvoering via de worker."""

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from shc_integrations.base import ButtonEvent
from sqlalchemy import select

from shc_api import backup, cli
from shc_api import db as db_module
from shc_api.commissioning import lock_commissioning, read_gate
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import ButtonMapping, CommandRecord, Device, Home
from shc_api.routers import system
from shc_api.store import get_setting, set_setting

from .boiler_support import commands, protect
from .conftest import ADMIN_PW, Session
from .test_inbedrijfstelling_beheer import wait_for_lock_queue
from .test_inbedrijfstelling_groepen import change, mapping
from .test_inbedrijfstelling_groepen import group_rig as group_rig
from .test_inbedrijfstelling_handhaving import mark, set_gate, snapshot
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, make_v1, temp_db
from .test_ws_climate_worker import make_rig
from .ws_devices_support import add_integration, new_home


@pytest.fixture(autouse=True)
async def isolated_import_database(database, monkeypatch, tmp_path):
    """Hou sessies, woningen, back-ups en herstel van deze proeven in hun eigen database."""
    async with temp_db() as url:
        with monkeypatch.context() as local:
            local.setenv("SHC_DATABASE_URL", url)
            local.setenv("SHC_BACKUP_DIR", str(tmp_path / "backups"))
            local.setenv("SHC_DATA_DIR", str(tmp_path / "data"))
            local.setattr(db_module, "_engine", None)
            local.setattr(db_module, "_sessionmaker", None)
            get_settings.cache_clear()
            try:
                async with sessionmaker()() as db:
                    await set_setting(db, "safe_mode", False)
                    await db.commit()
                await set_gate(True)
                yield
            finally:
                await db_module.dispose()
                get_settings.cache_clear()


async def save_backup(admin, tmp_path, password, format_number):
    made = await admin.post("/system/backups", json={"passphrase": password})
    assert made.status_code == 200, made.text
    path = get_settings().backup_dir / made.json()["filename"]
    if format_number != "v2":
        manifest, files = backup.open_archive(path.read_bytes(), password)
        tables: dict[str, list[dict]] = {}
        for name, blob in files.items():
            if name.startswith("tables/"):
                table = name.split("/")[1]
                tables.setdefault(table, []).extend(json.loads(line) for line in blob.splitlines() if line)
        revision = manifest["schema_revision"]
        if format_number == "v1-0004":
            revision = "0004"
            tables["settings"] = [row for row in tables["settings"] if row["key"] != "commissioning_gate"]
            for row in tables["devices"]:
                row.pop("detection_method")
                row.pop("fallback")
        path = tmp_path / "oud.shcbak"
        path.write_bytes(make_v1(tables, revision, password))
    return path


async def restore_backup(admin, tmp_path, path, password, entrance, mode):
    if entrance == "web":
        verified = await _upload(admin, path.read_bytes(), password)
        assert verified.status_code == 200 and verified.json()["restorable"], verified.text
        response = await admin.post("/system/restore/apply", json={
            "upload_id": verified.json()["upload_id"], "passphrase": password, "mode": mode,
        })
        assert response.status_code == 202, response.text
        for _ in range(600):
            result = await admin.c.get(f"/api/v1/system/restore/jobs/{response.json()['job_id']}")
            assert result.status_code == 200, result.text
            job = result.json()
            if job["state"] != "running":
                break
            await asyncio.sleep(0.05)
        assert job["state"] == "done", job
    else:
        passfile = tmp_path / "wachtwoordzin"
        passfile.write_text(password)
        passfile.chmod(0o600)
        args = argparse.Namespace(file=str(path), passphrase_file=str(passfile), mode=mode, yes=True, z2m_dir=None)
        assert await cli._restore(args, dry_run=False) == 0
    login = await admin.c.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    return Session(admin.c, login.json()["csrf_token"])


async def test_resultaat_van_voor_webherstel_overschrijft_geen_archiefrij(admin, tmp_path, monkeypatch):
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)
    rig = await make_rig(admin)
    await mark(rig, "tested")
    z = rig.zones["Woonkamer"]
    queued = await admin.post(f"/zones/{z}/command", json={"mode": "heat"})
    assert queued.status_code == 202
    cid = uuid.UUID(queued.json()["command_id"])
    backup_path = await save_backup(admin, tmp_path, PW, "v2")
    entered, send, recording, record = [asyncio.Event() for _ in range(4)]
    adapter = rig.fakes["Woonkamer"]
    execute = adapter.execute
    record_result = rig.w._record_result

    async def wait_to_send(command):
        entered.set()
        await send.wait()
        return await execute(command)

    async def wait_to_record(*args, **kwargs):
        recording.set()
        await record.wait()
        return await record_result(*args, **kwargs)

    monkeypatch.setattr(adapter, "execute", wait_to_send)
    monkeypatch.setattr(rig.w, "_record_result", wait_to_record)
    work = asyncio.create_task(rig.run_commands())
    restore = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        restore = asyncio.create_task(restore_backup(admin, tmp_path, backup_path, PW, "web", "same_home"))
        await wait_for_lock_queue(1)
        send.set()
        await asyncio.wait_for(recording.wait(), 5)
        done, _ = await asyncio.wait({restore}, timeout=1)
        assert not done, "Herstel kreeg de vergrendeling vóór boeking van het adapterresultaat."
    finally:
        send.set()
        record.set()
        await asyncio.wait_for(asyncio.gather(*(task for task in (work, restore) if task)), 15)
    async with sessionmaker()() as db:
        row = await db.get(CommandRecord, cid)
        assert (row.status, row.attempts) == ("queued", 0)
        assert await get_setting(db, "safe_mode") is True
    assert len(adapter.calls) == 1


async def test_herstelde_hue_opdracht_met_afwijkend_doel_blijft_dicht(admin, group_rig, tmp_path, monkeypatch):
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)
    r = group_rig
    async with sessionmaker()() as db:
        cmd = CommandRecord(home_id=r.home, device_id=r.members[0], action="set_light", status="queued",
                            params={"id": r.inv.L2, "on": True}, source="user", actor="proef")
        db.add(cmd)
        await db.commit()
    path = await save_backup(admin, tmp_path, PW, "v2")
    restored = await restore_backup(admin, tmp_path, path, PW, "web", "same_home")
    assert (await restored.post("/system/safe-mode/release")).status_code == 200
    await r.worker.process_commands({r.home})
    assert not r.fake.calls
    async with sessionmaker()() as db:
        assert (await db.get(CommandRecord, cmd.id)).status == "denied"


@pytest.mark.parametrize("entrance", ["web", "cli"])
@pytest.mark.parametrize("format_number", ["v1-0004", "v1", "v2"])
@pytest.mark.parametrize("condition", ["tested", "untested", "protected", "new_home"])
async def test_import_vrijgave_behoudt_gate_tot_adapter(
    admin, group_rig, tmp_path, monkeypatch, entrance, format_number, condition,
):
    r = group_rig
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)
    group_path = f"/lights/groups/{r.group}"
    queued = await admin.post(group_path, json={"on": True})
    assert queued.status_code == 202, queued.text
    if condition in {"untested", "protected"}:
        await change(r.members[1], compatibility="untested", tested_at=None)
    if condition == "protected":
        await protect(r.home, r.members[0])
    async with sessionmaker()() as db:
        row = ButtonMapping(home_id=r.home, device_id=r.button, **mapping(r, False))
        db.add(row)
        await db.commit()
        mid = row.id
    password = uuid.uuid4().hex
    await set_gate(False)
    path = await save_backup(admin, tmp_path, password, format_number)
    await set_gate(True)
    mode = "new_home" if condition == "new_home" else "same_home"
    restored = await restore_backup(admin, tmp_path, path, password, entrance, mode)
    async with sessionmaker()() as db:
        assert (await read_gate(db))["enabled"] is True
        assert await get_setting(db, "safe_mode") is True
        assert await db.get(ButtonMapping, mid) is not None
        old_home = await db.get(Home, r.home)
        assert (old_home.active_to is not None) == (mode == "new_home")
    response = await restored.post("/system/safe-mode/release")
    assert response.status_code == 200 and response.json()["safe_mode"] is False, response.text
    async with sessionmaker()() as db:
        assert (await read_gate(db))["enabled"] is True, "Vrijgave heeft de vooraf ingeschakelde gate uitgezet."
    if mode == "new_home":
        before = await snapshot()
        denied = await restored.post(group_path, json={"on": True})
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "policy_denied", denied.text
        assert await snapshot() == before
        async with sessionmaker()() as db:
            new = (await db.execute(select(Home).where(Home.active_to.is_(None)))).scalar_one()
        assert await commands(new.id) == []
        fresh = await add_integration(new.id, "hue")
        await r.worker.handle_events(fresh, r.model.snapshot(datetime.now(UTC)))
        async with sessionmaker()() as db:
            found = list((await db.execute(select(Device).where(Device.integration_id == fresh.id))).scalars())
        assert found and {d.compatibility for d in found} == {"untested"}
        new_group = next(d for d in found if d.stable_identity == r.inv.G_LIVING)
        group_path = f"/lights/groups/{new_group.id}"
        denied = await restored.post(group_path, json={"on": True})
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "device_not_tested", denied.text
        for d in found:
            if d.stable_identity in {r.inv.G_LIVING, r.inv.L1, r.inv.L2}:
                assert (await restored.post(f"/devices/{d.id}/tested", json={})).status_code == 200
        assert (await restored.post(group_path, json={"on": True})).status_code == 202
        await r.worker.process_commands()
        assert len(r.fake.calls) == 1, "Een opdracht uit de gearchiveerde woning bereikte de adapter."
        assert (await commands(new.id))[0].status == "confirmed"
        archived_command = (await commands(r.home))[0]
        assert archived_command.status == "denied", "De oude wachtrij van de gearchiveerde woning is niet geweigerd."
        return

    code = {"untested": "device_not_tested", "protected": "policy_denied"}.get(condition)
    before = await snapshot()
    response = await restored.post(group_path, json={"on": True})
    assert response.status_code == (409 if code else 202), response.text
    if code:
        assert response.json()["detail"]["code"] == code
        assert await snapshot() == before
    event = ButtonEvent(r.inv.D_DIMMER, r.inv.B3, "short_release", datetime.now(UTC), event_id=uuid.uuid4().hex)
    await r.worker.handle_events(r.integ, [event])
    await r.worker.handle_events(r.integ, [event])
    await r.worker.process_commands({r.home})
    rows = await commands(r.home)
    old = next(row for row in rows if str(row.id) == queued.json()["command_id"])
    assert old.status == ("denied" if code else "confirmed")
    assert len(rows) == (1 if code else 3)
    assert len(r.fake.calls) == (0 if code else 3)


@pytest.mark.parametrize("operation", ["vrijgave", "archiveren"])
async def test_herstel_beheertransactie_gebruikt_dezelfde_lock_en_behoudt_gate(admin, operation):
    home_id = await new_home(admin, "Vergrendelproef")
    path = "/system/safe-mode/release" if operation == "vrijgave" else f"/homes/{home_id}/archive"
    async with sessionmaker()() as db:
        await set_setting(db, "safe_mode", True)
        await db.commit()
        await lock_commissioning(db)
        release = asyncio.create_task(admin.post(path))
        try:
            done, _ = await asyncio.wait({release}, timeout=0.15)
            assert not done, "De beheerroute liep al door terwijl de herstelvergrendeling actief was."
        finally:
            await db.commit()
            response = await asyncio.wait_for(release, timeout=10)
    assert response.status_code == 200, response.text
    field = "safe_mode" if operation == "vrijgave" else "active"
    assert response.json()[field] is False, response.text
    async with sessionmaker()() as db:
        assert (await read_gate(db))["enabled"] is True
