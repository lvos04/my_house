"""Backend follow-ups after wave 1: CLI reset-password closes sockets, device-reported command errors,
the backup's OOM score, alert acknowledgement rights, Systeem events/addresses, pairing hosts."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest
from sqlalchemy import func, select

from shc_api import cli, events
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, CommandRecord, Home, Integration
from shc_api.models import Session as WebSession
from shc_api.security import lan_url_problem, remote_url_problem
from shc_api.store import get_setting, raise_alert, set_setting

from .conftest import Session
from .ws_devices_support import add_device, add_integration, attach, make_worker, new_home

PW = "follow-up-wachtwoord-1"


@pytest.fixture(autouse=True)
async def _default_home_first(home):
    """The shared default home must exist before these tests add their own homes."""
    return home


@contextlib.asynccontextmanager
async def notifications():
    """Every NOTIFY on the events channel while the block runs, as decoded dicts (real LISTEN)."""
    conn = await asyncpg.connect(get_settings().database_url.replace("+asyncpg", ""))
    got: list[dict] = []
    await conn.add_listener(events.CHANNEL, lambda _c, _p, _ch, payload: got.append(json.loads(payload)))
    try:
        yield got
    finally:
        await conn.close()


async def wait_for(pred, limit_s: float = 5.0):
    deadline = asyncio.get_running_loop().time() + limit_s
    while True:
        result = pred()
        if result:
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.05)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _user(admin, role="viewer") -> str:
    name = f"fu_{uuid.uuid4().hex[:8]}"
    r = await admin.post("/users", json={"username": name, "password": PW, "role": role})
    assert r.status_code == 200, r.text
    return name


async def _login(c, username, password=PW) -> Session:
    r = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return Session(c, r.json()["csrf_token"])


@contextlib.asynccontextmanager
async def safe_mode(value: bool):
    async with sessionmaker()() as db:
        before = bool(await get_setting(db, "safe_mode", False))
        await set_setting(db, "safe_mode", value)
        await db.commit()
    try:
        yield
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "safe_mode", before)
            await db.commit()


# --- 1. CLI reset-password publishes auth.revoked --------------------------------------------------


async def test_cli_reset_password_publishes_auth_revoked(admin, app):
    name = await _user(admin, "admin")
    async with _client(app) as c:
        await _login(c, name)
    async with sessionmaker()() as db:
        from shc_api.models import User

        uid = (await db.execute(select(User.id).where(User.username == name))).scalar_one()
        assert (await db.execute(select(func.count()).where(WebSession.user_id == uid))).scalar_one() == 1
    async with notifications() as got:
        out = await cli.reset_password(name, "een-heel-nieuw-wachtwoord")
        msg = await wait_for(lambda: next((m for m in got if m["type"] == events.AUTH_REVOKED), None))
    assert out == {"sessions": 1, "phones_revoked": 0}
    assert msg["data"]["user_id"] == str(uid) and msg["data"]["reason"] == "password_reset"
    assert msg["data"]["sessions"] is True and msg["data"]["clients"] is True
    async with sessionmaker()() as db:
        assert (await db.execute(select(func.count()).where(WebSession.user_id == uid))).scalar_one() == 0


# --- 2. a command the device/hub reports an error for is "failed" (item 27) ------------------------


class FakeHue:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, command):
        self.calls.append(command)
        return self.result


@pytest.mark.parametrize("flagged", [True, False])
async def test_bridge_error_result_is_stored_as_failed(admin, flagged):
    from shc_integrations.base import CommandResult

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", "Hue Bridge", config={"host": "192.168.1.9"})
    group = await add_device(home_id, "light_group", "Woonkamer", provider="hue", integration_id=integ.id)
    detail = "Hue Bridge meldt: device (Plafond) has communication issues"
    # flagged=False: an adapter without the `rejected` flag; the message prefix is the fallback.
    fake = FakeHue(CommandResult(ok=True, confirmed=False, detail=detail, rejected=flagged))
    w = make_worker()
    attach(w, integ, fake)
    async with sessionmaker()() as db:
        cmd = CommandRecord(
            home_id=home_id, device_id=group.id, action="set_group", params={"on": True}, source="user", actor="t"
        )
        db.add(cmd)
        await db.commit()
    async with safe_mode(False), notifications() as got:
        await w.process_commands({home_id})
        upd = await wait_for(
            lambda: next((m for m in got if m["type"] == "command.updated" and m["data"]["id"] == str(cmd.id)), None)
        )
    assert fake.calls and upd["data"]["status"] == "failed" and upd["data"]["detail"] == detail
    async with sessionmaker()() as db:
        row = await db.get(CommandRecord, cmd.id)
        assert row.status == "failed" and row.detail == detail
    lights = (await admin.get(f"/homes/{home_id}/lights")).json()
    g = next(x for x in lights["groups"] if x["id"] == str(group.id))
    assert g["last_command"]["status"] == "failed"  # the same answer as command.updated


async def test_unconfirmed_without_error_stays_sent(admin):
    from shc_integrations.base import CommandResult

    from shc_api.worker.commands import device_reported_error

    assert not device_reported_error(CommandResult(ok=True, confirmed=False, detail="readback wijkt af"))
    assert device_reported_error(CommandResult(ok=True, confirmed=False, detail="x", rejected=True))
    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", "Hue Bridge", config={"host": "192.168.1.9"})
    group = await add_device(home_id, "light_group", "Keuken", provider="hue", integration_id=integ.id)
    w = make_worker()
    attach(w, integ, FakeHue(CommandResult(ok=True, confirmed=False, detail="Hue Bridge bevestigt niet")))
    async with sessionmaker()() as db:
        cmd = CommandRecord(
            home_id=home_id, device_id=group.id, action="set_group", params={"on": True}, source="user", actor="t"
        )
        db.add(cmd)
        await db.commit()
    async with safe_mode(False):
        await w.process_commands({home_id})
    async with sessionmaker()() as db:
        assert (await db.get(CommandRecord, cmd.id)).status == "sent"


# --- 3. the backup child process volunteers for the OOM killer --------------------------------------


def test_prefer_oom_kill_raises_only(tmp_path):
    from shc_api.worker.backup_job import OOM_SCORE_ADJ, prefer_oom_kill

    f = tmp_path / "oom_score_adj"
    f.write_text("0\n")
    assert prefer_oom_kill(path=f) is True and f.read_text().strip() == str(OOM_SCORE_ADJ)
    f.write_text("900\n")
    assert prefer_oom_kill(path=f) is True and f.read_text().strip() == "900"  # never lowered
    assert prefer_oom_kill(path=tmp_path / "missing" / "oom_score_adj") is False  # best effort, no error


@pytest.mark.skipif(not Path("/proc/self/oom_score_adj").exists(), reason="Linux only")
def test_prefer_oom_kill_in_a_real_child_process():
    code = (
        "from shc_api.worker.backup_job import prefer_oom_kill; ok = prefer_oom_kill(); "
        "print(ok, open('/proc/self/oom_score_adj').read().strip())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    ok, value = out.stdout.split()
    assert ok == "True" and int(value) >= 500


async def test_cli_backup_sets_oom_score_first(monkeypatch):
    import argparse

    from shc_api.worker import backup_job

    calls = []
    monkeypatch.setattr(backup_job, "prefer_oom_kill", lambda: calls.append("oom") or True)

    async def fake_auto():
        calls.append("backup")
        return {"status": "ok", "filename": "x"}

    monkeypatch.setattr(backup_job, "run_auto_backup", fake_auto)
    args = argparse.Namespace(auto=True, json=True)
    assert await cli._backup(args) == 0
    assert calls == ["oom", "backup"]


# --- 4. acknowledging an alert is a mutation: Control role -----------------------------------------


async def test_alert_ack_needs_control(admin, app, home):
    async with sessionmaker()() as db:
        a = await raise_alert(db, uuid.UUID(home["id"]), f"fu:{uuid.uuid4().hex}", "warn", "Testmelding", "", "test")
        await db.commit()
        alert_id = a.id
    viewer = await _user(admin)
    async with _client(app) as c:
        v = await _login(c, viewer)
        r = await v.post(f"/alerts/{alert_id}/ack")
        assert r.status_code == 403
    r = await admin.post(f"/alerts/{alert_id}/ack")
    assert r.status_code == 200 and r.json()["acknowledged"] is True
    async with sessionmaker()() as db:
        assert (await db.get(Alert, alert_id)).acknowledged_at is not None


# --- 5. Systeem: shared address rules, status.updated, archive stops the worker ---------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.20:8088",
        "http://nas.local/",
        "http://huis.voorbeeld.nl",
        "http://127.0.0.1",
        "http://192.168.1.20/pad",
        "ftp://192.168.1.2",
        "https://huis.voorbeeld.nl",
        "https://user:pw@huis.voorbeeld.nl",
    ],
)
async def test_settings_and_pairing_use_the_same_address_rules(admin, url):
    before = (await admin.get("/system/settings")).json()
    for key, problem in (("lan_url", lan_url_problem), ("remote_url", remote_url_problem)):
        r = await admin.put("/system/settings", {key: url})
        expected = problem(url)
        if expected is None:
            assert r.status_code == 200, r.text
        else:
            assert r.status_code == 422 and expected in r.json()["detail"]["message"], r.text
    await admin.put("/system/settings", {"lan_url": before["lan_url"], "remote_url": before["remote_url"]})


async def test_address_with_a_path_gets_a_specific_message(admin):
    r = await admin.put("/system/settings", {"lan_url": "http://192.168.1.20:8088/app"})
    assert r.status_code == 422 and "zonder pad" in r.json()["detail"]["message"]
    r = await admin.put("/system/settings", {"remote_url": "https://huis.voorbeeld.nl/?x=1"})
    assert r.status_code == 422 and "Adres buiten huis" in r.json()["detail"]["message"]


async def test_settings_readable_for_viewers(admin, app):
    viewer = await _user(admin)
    async with _client(app) as c:
        v = await _login(c, viewer)
        r = await v.get("/system/settings")
        assert r.status_code == 200 and {"lan_url", "remote_url", "safe_mode"} <= set(r.json())
        assert (await v.put("/system/settings", {"lan_url": None})).status_code == 403


async def test_release_safe_mode_publishes_status(admin, home):
    async with safe_mode(True):
        async with notifications() as got:
            r = await admin.post("/system/safe-mode/release")
            assert r.status_code == 200
            msg = await wait_for(
                lambda: next((m for m in got if m["type"] == "status.updated" and m["home_id"] == home["id"]), None)
            )
    assert "safe_mode" not in {c["key"] for c in msg["data"]}  # safe mode is off in the new status


async def test_cli_maintenance_publishes_status(home):
    async with notifications() as got:
        await cli.set_maintenance(True)
        try:
            on = await wait_for(lambda: next((m for m in got if m["type"] == "status.updated"), None))
        finally:
            await cli.set_maintenance(False)
    worker = next(c for c in on["data"] if c["key"] == "worker")
    assert "Onderhoudsmodus" in worker["detail"]


async def test_archive_home_stops_its_integrations_at_once(admin, monkeypatch):
    """archive_home publishes integration.changed per integration; the worker's LISTEN wakes the reload
    loop, which stops the adapters of the archived home (no 2-minute wait)."""
    from shc_api.worker import core

    class Fake:
        closed = False

        def __init__(self, config, credentials, **kw):
            pass

        async def poll(self):
            return []

        async def subscribe(self):
            return
            yield

        async def close(self):
            self.closed = True

    from shc_integrations import registry

    monkeypatch.setitem(registry.ADAPTERS, "homewizard", Fake)
    monkeypatch.setitem(core.POLL_INTERVAL, "homewizard", 3600)
    home_id = await new_home(admin)
    i1 = await add_integration(home_id, "homewizard", "Meter 1", config={"host": "192.168.1.31"})
    i2 = await add_integration(home_id, "homewizard", "Meter 2", config={"host": "192.168.1.32"})
    w = make_worker()
    await w.load_integrations({home_id})
    adapters = {i1.id: w.adapters[i1.id], i2.id: w.adapters[i2.id]}
    listener = asyncio.create_task(w.listen())
    try:
        await asyncio.sleep(0.3)  # LISTEN connected
        async with notifications() as got:
            r = await admin.post(f"/homes/{home_id}/archive")
            assert r.status_code == 200
            await wait_for(lambda: w.wake_reload.is_set())
            changed = await wait_for(
                lambda: {m["data"]["id"] for m in got if m["type"] == "integration.changed"} >= {str(i1.id), str(i2.id)}
            )
            assert changed
            await wait_for(lambda: any(m["type"] == "status.updated" and m["home_id"] == str(home_id) for m in got))
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
    await w.load_integrations({home_id})  # what the woken reload loop runs
    assert not w.adapters and all(a.closed for a in adapters.values())
    async with sessionmaker()() as db:
        assert (await db.get(Home, home_id)).active_to is not None
        assert not any((await db.execute(select(Integration.enabled).where(Integration.home_id == home_id))).scalars())
    for tasks in w.integ_tasks.values():
        for t in tasks:
            t.cancel()


# --- 6. pairing allowed hosts come from Settings ---------------------------------------------------


async def test_pairing_allowed_hosts_setting(monkeypatch):
    from shc_api.routers.devices import validate_host

    with pytest.raises(ValueError):
        validate_host("mosquitto")
    monkeypatch.setattr(get_settings(), "pairing_allowed_hosts", "mosquitto")
    assert validate_host("mosquitto") == "mosquitto"


# --- 7. a stuck loop ends the worker process so Docker restarts it (item 28) ------------------------


def _stuck_worker(exits: list[int]):
    from shc_api.worker import Worker

    w = Worker(exit_fn=exits.append)
    w.loop_interval = {"climate": 60.0, "heartbeat": 30.0}
    w.loop_seen = {"climate": time.monotonic(), "heartbeat": time.monotonic()}
    return w


def test_exit_if_stuck_logs_and_exits_non_zero(caplog):
    exits: list[int] = []
    w = _stuck_worker(exits)
    assert w.exit_if_stuck() == [] and exits == []
    w.loop_seen["climate"] = time.monotonic() - (5 * 60 + 61)
    with caplog.at_level(logging.CRITICAL, logger="shc.worker"):
        assert w.exit_if_stuck() == ["climate"]
    assert exits == [1]
    msg = next(r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)
    assert "climate" in msg and "code 1" in msg and "opnieuw" in msg


def test_watchdog_thread_exits_even_when_the_event_loop_is_blocked():
    exits: list[int] = []
    fired = threading.Event()
    w = _stuck_worker(exits)
    w.exit_fn = lambda code: (exits.append(code), fired.set())
    t = w.start_watchdog(check_s=0.02)
    try:
        time.sleep(0.1)
        assert exits == []  # nothing stuck: the watchdog keeps watching
        w.loop_seen["heartbeat"] = time.monotonic() - 1000
        # Block like a synchronous call inside a coroutine would: only a thread can still act.
        assert fired.wait(2.0)
        assert exits == [1]
        t.join(1.0)
        assert not t.is_alive()  # it fires once
    finally:
        w._watchdog_stop.set()


async def test_run_starts_and_stops_the_watchdog():
    exits: list[int] = []
    w = _stuck_worker(exits)

    async def nothing(*_a, **_k):
        return None

    async def forever():
        await asyncio.Event().wait()

    w.load_integrations = nothing
    w.loop_specs = lambda: []
    w.listen = forever
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.05)
    assert any(t.name == "shc-worker-watchdog" and t.is_alive() for t in threading.enumerate())
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert w._watchdog_stop.is_set() and exits == []


# --- 8. status: backup details, maintenance, last restore ---------------------------------------


@contextlib.asynccontextmanager
async def settings_restored(*keys):
    async with sessionmaker()() as db:
        before = {k: await get_setting(db, k) for k in keys}
    try:
        yield
    finally:
        async with sessionmaker()() as db:
            for k, v in before.items():
                await set_setting(db, k, v)
            await db.commit()


async def test_backup_check_shows_versions_zigbee_and_auto_failures(admin):
    from shc_api.models import BackupRecord
    from shc_api.views.status import backup_check

    home_id = await new_home(admin)
    await add_integration(home_id, "zigbee", "Zigbee")  # Zigbee in use: its data belongs in the backup
    async with settings_restored("backup_state"), sessionmaker()() as db:
        rec = BackupRecord(
            kind="export",
            filename=f"fu-{uuid.uuid4().hex}.shcbak",
            status="ok",
            auto=True,
            app_version="9.8.7",
            schema_revision="0003_followups",
            detail={"zigbee2mqtt_included": False},
        )
        db.add(rec)
        await set_setting(db, "backup_state", {"failures": 0})
        await db.commit()
        try:
            c = await backup_check(db)
            assert c["state"] == "warn" and c["zigbee2mqtt_included"] is False
            assert "Zigbee in laatste back-up: nee" in c["detail"] and "app 9.8.7, schema 0003_followups" in c["detail"]
            rec.detail = {"zigbee2mqtt_included": True}
            await db.commit()
            c = await backup_check(db)
            assert c["state"] == "ok" and "Zigbee in laatste back-up: ja" in c["detail"]
            assert (c["app_version"], c["schema_revision"], c["auto_failures"]) == ("9.8.7", "0003_followups", 0)
            await set_setting(db, "backup_state", {"failures": 2, "last_error": "Schijf vol"})
            await db.commit()
            c = await backup_check(db)
            assert c["state"] == "warn" and c["auto_failures"] == 2 and c["auto_last_error"] == "Schijf vol"
            assert "Automatische back-up mislukt (2x): Schijf vol" in c["detail"]
        finally:
            await db.delete(rec)
            await db.commit()


async def test_maintenance_and_restore_report_in_status(admin, home):
    from shc_api.views.status import status_checks, system_checks

    report = {
        "schema_from": "0002_improvements",
        "schema_to": "0003_followups",
        "total_records": 1234,
        "dropped": {"tables": {"oude_tabel": 3}, "columns": {}, "rows_removed_by_migration": {}},
        "mode": "same_home",
        "zigbee2mqtt_included": True,
    }
    async with (
        settings_restored("maintenance", "safe_mode", "restore_report", "restored_from"),
        sessionmaker()() as db,
    ):
        await set_setting(db, "maintenance", True)
        await set_setting(db, "safe_mode", True)
        await set_setting(db, "restore_report", report)
        await set_setting(db, "restored_from", {"restored_at": "2026-11-08T10:00:00+00:00", "app_version": "0.1.0"})
        await db.commit()
        overview = {c["key"]: c for c in await status_checks(db, uuid.UUID(home["id"]))}
        assert overview["maintenance"]["state"] == "warn" and "herstel bezig" in overview["maintenance"]["detail"]
        restore = overview["restore"]
        assert restore["state"] == "warn" and restore["report"] == report
        assert "1234 records" in restore["detail"] and "oude_tabel" in restore["detail"]
        assert "08-11 10:00 UTC" in restore["detail"]
        system = {c["key"] for c in await system_checks(db)}
        assert {"maintenance", "restore"} <= system
        await set_setting(db, "maintenance", False)
        await set_setting(db, "safe_mode", False)
        await db.commit()
        overview = {c["key"] for c in await status_checks(db, uuid.UUID(home["id"]))}
        assert "maintenance" not in overview and "restore" not in overview  # overview: only right after
        assert "restore" in {c["key"] for c in await system_checks(db)}  # the System page keeps it


# --- 9. migration 0003: pairing_codes.role, zone_log indexes (scratch database) --------------------


async def test_migration_0003_upgrade_and_downgrade():
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from shc_api import backup as bk

    from .test_ws_backup_format import temp_db

    async def sql(url, stmt, params=None, fetch=True):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as c:
                res = await c.execute(text(stmt), params or {})
                return res.all() if fetch else None
        finally:
            await engine.dispose()

    async def indexes(url):
        return {r[0] for r in await sql(url, "SELECT indexname FROM pg_indexes WHERE tablename = 'zone_log'")}

    uid, open_admin, open_plain, open_bad, used = (uuid.uuid4() for _ in range(5))
    async with temp_db("0002") as url:
        assert "ix_zone_log_zone_id" in await indexes(url)
        await sql(
            url,
            "INSERT INTO users (id, username, password_hash, role, disabled, created_at) "
            "VALUES (:id, 'm', 'x', 'admin', false, now())",
            {"id": uid},
            fetch=False,
        )
        for cid, is_used in ((open_admin, False), (open_plain, False), (open_bad, False), (used, True)):
            await sql(
                url,
                "INSERT INTO pairing_codes (id, code_hash, user_id, label, expires_at, used_at) VALUES "
                "(:id, :h, :u, 'c', now() + interval '10 minutes', CASE WHEN :used THEN now() END)",
                {"id": cid, "h": uuid.uuid4().hex, "u": uid, "used": is_used},
                fetch=False,
            )
        for cid, role in ((open_admin, "admin"), (open_bad, "superuser"), (used, "viewer")):
            await sql(
                url,
                "INSERT INTO settings (key, value) VALUES (:k, jsonb_build_object('v', CAST(:r AS text)))",
                {"k": f"pairing_role:{cid}", "r": role},
                fetch=False,
            )

        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "0003")
        roles = dict(await sql(url, "SELECT id, role FROM pairing_codes"))
        assert roles == {open_admin: "admin", open_plain: "control", open_bad: "control", used: "viewer"}
        assert not await sql(url, "SELECT key FROM settings WHERE key LIKE 'pairing\\_role:%'")
        idx = await indexes(url)
        assert "ix_zone_log_at" in idx and "ix_zone_log_zone_at" in idx and "ix_zone_log_zone_id" not in idx
        with pytest.raises(Exception, match="ck_pairing_codes_role"):
            await sql(url, "UPDATE pairing_codes SET role = 'superuser'", fetch=False)

        await asyncio.to_thread(command.downgrade, bk.alembic_config(url), "0002")
        cols = {
            r[0]
            for r in await sql(
                url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'pairing_codes'"
            )
        }
        assert "role" not in cols
        back = dict(await sql(url, "SELECT key, value->>'v' FROM settings WHERE key LIKE 'pairing\\_role:%'"))
        # Open codes get their role back as settings rows (the 0002 workaround); the used code does not.
        assert back == {
            f"pairing_role:{open_admin}": "admin",
            f"pairing_role:{open_plain}": "control",
            f"pairing_role:{open_bad}": "control",
        }
        idx = await indexes(url)
        assert "ix_zone_log_zone_id" in idx and "ix_zone_log_at" not in idx

        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "head")
        assert (await sql(url, "SELECT version_num FROM alembic_version"))[0][0] == bk.head_revision() == "0003"
        assert dict(await sql(url, "SELECT id, role FROM pairing_codes"))[open_admin] == "admin"


# --- 10. restore as a new home keeps comfort profiles and schedules as drafts (plan §8.5) ----------


async def test_restore_new_home_copies_profiles_and_schedules_as_drafts(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from shc_api import backup as bk

    from .test_ws_backup_format import PW as BACKUP_PW
    from .test_ws_backup_format import temp_db

    async def run(url, stmt, **params):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as c:
                res = await c.execute(text(stmt), params)
                return res.all() if res.returns_rows else None
        finally:
            await engine.dispose()

    hid, pid, living, attic = (uuid.uuid4() for _ in range(4))
    week = json.dumps([0, 1, 2, 3, 4, 5, 6])
    async with temp_db() as url:
        await run(
            url,
            "INSERT INTO homes (id, name, timezone, currency, active_from) VALUES (:h, 'Oud', 'UTC', 'EUR', now())",
            h=hid,
        )
        await run(
            url,
            "INSERT INTO comfort_profiles (id, home_id, name, target_c, band_c, mode, energy_priority, schedule) "
            "VALUES (:p, :h, 'Winter', 21.5, 0.4, 'heat', 'saving', CAST(:s AS jsonb))",
            p=pid,
            h=hid,
            s=json.dumps(
                [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "07:00", "target_c": 21.5, "mode": None, "label": ""}]
            ),
        )
        for zid, name, profile_id, policy in (
            (living, "woonkamer", pid, {"band_c": 0.4, "energy_priority": "saving", "max_offset_c": 2.5}),
            (attic, "Zolder", None, {}),
        ):
            await run(
                url,
                "INSERT INTO climate_zones (id, home_id, name, profile, policy, control, desired_mode, last_commanded, "
                "start_times, adaptive_enabled, profile_id, correction_c) VALUES (:z, :h, :n, 'Winter', CAST(:pol AS jsonb), "
                "'schedule', 'heat', '{}', '[]', true, :pid, 1.5)",
                z=zid,
                h=hid,
                n=name,
                pol=json.dumps(policy),
                pid=profile_id,
            )
            for start, target, mode in (("07:00", 21.5, "heat"), ("22:30", 18.0, None)):
                await run(
                    url,
                    "INSERT INTO schedule_blocks (id, zone_id, days, start, target_c, mode, label) "
                    "VALUES (:id, :z, CAST(:d AS jsonb), :s, :t, :m, 'dag')",
                    id=uuid.uuid4(),
                    z=zid,
                    d=week,
                    s=start,
                    t=target,
                    m=mode,
                )
        rec = await bk.run_backup(BACKUP_PW, True, url=url, backup_dir=tmp_path)
        r = (await bk.restore_file(tmp_path / rec["filename"], BACKUP_PW, url=url, mode="new_home")).as_dict()
        new = uuid.UUID(r["new_home_id"])
        assert r["drafts"]["profiles"] == 1
        zones_report = {z["name"]: z for z in r["drafts"]["zones"]}
        assert zones_report["Zolder"]["copied_to"] is None and zones_report["Zolder"]["blocks"] == 2
        assert zones_report["woonkamer"]["blocks"] == 2

        profiles = await run(
            url,
            "SELECT id, name, target_c, mode, energy_priority, schedule FROM comfort_profiles WHERE home_id = :h",
            h=new,
        )
        assert len(profiles) == 1 and profiles[0].id != pid
        assert (profiles[0].name, profiles[0].target_c, profiles[0].mode, profiles[0].energy_priority) == (
            "Winter",
            21.5,
            "heat",
            "saving",
        )
        assert profiles[0].schedule[0]["start"] == "07:00"
        zones = {z.name: z for z in await run(url, "SELECT * FROM climate_zones WHERE home_id = :h", h=new)}
        wk = zones["Woonkamer"]
        assert str(wk.id) == zones_report["woonkamer"]["copied_to"]
        # A draft: nothing runs until the user resumes the schedule; calibration starts again.
        assert wk.control == "off" and wk.profile_id == profiles[0].id and wk.profile == "Winter"
        assert wk.policy == {"band_c": 0.4, "energy_priority": "saving"} and wk.correction_c == 0
        assert wk.unit_device_id is None and wk.adaptive_enabled is False
        blocks = await run(
            url, "SELECT start, target_c, mode, label FROM schedule_blocks WHERE zone_id = :z ORDER BY start", z=wk.id
        )
        assert [tuple(b) for b in blocks] == [("07:00", 21.5, "heat", "dag"), ("22:30", 18.0, None, "dag")]
        assert zones["Slaapkamer"].control == "schedule"  # nothing copied: stays as a new zone
        assert not await run(url, "SELECT 1 FROM schedule_blocks WHERE zone_id = :z", z=zones["Slaapkamer"].id)
        # The archived home keeps its own profiles and schedules untouched.
        assert len(await run(url, "SELECT 1 FROM schedule_blocks WHERE zone_id IN (:a, :b)", a=living, b=attic)) == 4
        log = await run(url, "SELECT message FROM zone_log WHERE zone_id = :z", z=wk.id)
        assert any("concept" in m.message for m in log)


# --- 11. retention periods set in the app (item 19) ---------------------------------------------


async def test_retention_settings_validate_and_drive_the_cleanup(admin):
    from datetime import UTC, datetime, timedelta

    from shc_api.models import SensorReading, ZoneLog

    async with settings_restored("retention"):
        r = await admin.get("/system/retention")
        assert r.status_code == 200, r.text
        v = r.json()
        s = get_settings()
        assert (v["power_days"], v["counter_days"], v["log_days"]) == (
            s.retention_power_days,
            s.retention_counter_days,
            s.retention_log_days,
        )
        assert v["source"] == {"power_days": "env", "counter_days": "env", "log_days": "env"}
        assert v["minimums"] == {"power_days": 7, "counter_days": 90, "log_days": 7}
        for body, word in (
            ({"power_days": 3}, "minimaal 7"),
            ({"counter_days": 30}, "minimaal 90"),
            ({"log_days": 99999}, "maximaal"),
        ):
            r = await admin.put("/system/retention", body)
            assert r.status_code == 422 and word in r.json()["detail"]["message"], r.text
        assert (await admin.put("/system/retention", {"unknown_days": 9})).status_code == 422
        r = await admin.put("/system/retention", {"power_days": 14, "log_days": 10})
        assert r.status_code == 200
        v = r.json()
        assert (v["power_days"], v["log_days"]) == (14, 10) and v["source"]["power_days"] == "app"
        assert v["source"]["counter_days"] == "env"
        audit = (await admin.get("/audit?action=retention_updated&limit=1")).json()
        assert audit and audit[0]["detail"] == {"power_days": 14, "log_days": 10}

        home_id = await new_home(admin)
        meter = await add_device(home_id, "p1_meter", "P1 retentie")
        zone_id = (await admin.get(f"/homes/{home_id}/zones")).json()[0]["id"]
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            for days in (10, 20):
                db.add(
                    SensorReading(
                        device_id=meter.id,
                        metric="power_w",
                        value=1.0,
                        unit="W",
                        observed_at=now - timedelta(days=days),
                        event_id=f"retentie-{uuid.uuid4().hex}",
                    )
                )
                db.add(
                    ZoneLog(
                        zone_id=uuid.UUID(zone_id), at=now - timedelta(days=days - 1), kind="info", message=f"{days}"
                    )
                )
            await db.commit()
        w = make_worker()
        await w.retention_round()
        async with sessionmaker()() as db:
            left = (
                (await db.execute(select(SensorReading.observed_at).where(SensorReading.device_id == meter.id)))
                .scalars()
                .all()
            )
            logs = (
                (await db.execute(select(ZoneLog.message).where(ZoneLog.zone_id == uuid.UUID(zone_id)))).scalars().all()
            )
        assert len(left) == 1 and now - left[0] < timedelta(days=11)  # 20 days > 14: gone; 10 days: kept
        assert "10" in logs and "20" not in logs  # lines 9 and 19 days old, log_days 10

        r = await admin.put("/system/retention", {"power_days": None})
        assert r.json()["source"]["power_days"] == "env" and r.json()["power_days"] == s.retention_power_days
        assert r.json()["log_days"] == 10  # untouched
