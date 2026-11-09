"""Backup stream, HTTP side: backup settings, schedule with catch-up and back-off, automatic backups
in a separate process with pruning of only automatic ones, manual backups, restore via upload
(verify -> apply -> compare) and address validation (verbeterlijst 17, 32, 34, 43)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from shc_api import backup as bk
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, BackupRecord, Device
from shc_api.routers import system
from shc_api.store import get_setting, set_setting
from shc_api.worker import backup_job as bj

from .conftest import ADMIN_PW, Session

PW = "een lange wachtwoordzin"
AMS = ZoneInfo("Europe/Amsterdam")


class BW(bj.BackupMixin):
    def __init__(self):
        self.settings = get_settings()


def T(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


# --- schedule (pure) ----------------------------------------------------------------------------


def test_backup_due_first_opportunity_after_the_hour_with_catch_up():
    hour = 3
    # never made: due right away
    assert bj.backup_due(T("2026-11-08T10:00:00+01:00"), hour, AMS, None, {})
    last = T("2026-11-07T03:05:00+01:00")
    # 02:00 next day: younger than 23 h -> not yet
    assert not bj.backup_due(T("2026-11-08T02:00:00+01:00"), hour, AMS, last, {})
    # 03:10: past the hour, last one older than 23 h -> due
    assert bj.backup_due(T("2026-11-08T03:10:00+01:00"), hour, AMS, last, {})
    # NAS was off at 03:00; at 10:00 it catches up
    assert bj.backup_due(T("2026-11-08T10:00:00+01:00"), hour, AMS, last, {})
    # caught up at 10:00 yesterday: today it waits until 23 h have passed (09:00), not 03:00
    caught = T("2026-11-08T10:00:00+01:00")
    assert not bj.backup_due(T("2026-11-09T03:30:00+01:00"), hour, AMS, caught, {})
    assert bj.backup_due(T("2026-11-09T09:05:00+01:00"), hour, AMS, caught, {})
    # the hour follows the home's time zone (not hard-coded): 03:00 in New York
    ny = ZoneInfo("America/New_York")
    assert not bj.backup_due(T("2026-11-09T07:30:00+00:00"), hour, ny, T("2026-11-08T08:05:00+00:00"), {})
    assert bj.backup_due(T("2026-11-09T08:10:00+00:00"), hour, ny, T("2026-11-08T08:05:00+00:00"), {})


def test_failed_attempt_backs_off_instead_of_retrying_immediately():
    now = T("2026-11-08T10:00:00+00:00")
    st = {"failures": 1, "last_attempt": (now - timedelta(minutes=10)).isoformat()}
    assert not bj.backup_due(now, 3, AMS, None, st)
    assert bj.backup_due(now + timedelta(minutes=51), 3, AMS, None, st)
    st3 = {"failures": 3, "last_attempt": now.isoformat()}
    assert bj.retry_after(st3) == now + timedelta(hours=4)
    assert bj.retry_after({"failures": 9, "last_attempt": now.isoformat()}) == now + bj.MAX_BACKOFF
    assert bj.next_due(now, 3, AMS, None, st3) == now + timedelta(hours=4)


# --- settings -----------------------------------------------------------------------------------


async def test_backup_settings_passphrase_hour_and_retention(admin):
    r = await admin.get("/system/backup-settings")
    assert r.status_code == 200
    assert r.json()["passphrase_set"] is False and r.json()["enabled"] is False
    r = await admin.put("/system/backup-settings", {"passphrase": "te kort"})
    assert r.status_code == 422 and "minimaal 12 tekens" in r.json()["detail"]["message"]
    assert (await admin.put("/system/backup-settings", {"hour": 24})).status_code == 422
    assert (await admin.put("/system/backup-settings", {"keep": 0})).status_code == 422
    r = await admin.put("/system/backup-settings", {"passphrase": PW, "hour": 2, "keep": 3})
    s = r.json()
    assert r.status_code == 200 and s["passphrase_set"] and s["passphrase_source"] == "app"
    assert s["hour"] == 2 and s["keep"] == 3 and s["enabled"] is True
    assert PW not in r.text
    async with sessionmaker()() as db:
        assert PW not in str(await get_setting(db, "backup_passphrase_enc"))
        assert (await bj.backup_passphrase(db))[0] == PW
    r = await admin.put("/system/backup-settings", {"enabled": False})
    assert r.json()["enabled"] is False and r.json()["passphrase_set"] is True
    r = await admin.put("/system/backup-settings", {"clear_passphrase": True, "enabled": True})
    assert r.json()["passphrase_set"] is False


# --- automatic backups --------------------------------------------------------------------------


async def test_auto_backup_runs_in_a_separate_process_and_prunes_only_auto_backups(admin):
    s = get_settings()
    await admin.put("/system/backup-settings", {"passphrase": PW, "keep": 2, "enabled": True})
    async with sessionmaker()() as db:
        await set_setting(db, "last_auto_backup", None)
        await set_setting(db, "backup_state", {})
        await db.commit()
    manual = await admin.post("/system/backups", json={"passphrase": PW})
    assert manual.status_code == 200 and manual.json()["auto"] is False
    w = BW()
    await w.backup_round()
    assert w._backup_task is not None
    await w.backup_round()  # already running: no second process
    result = await asyncio.wait_for(w._backup_task, 120)
    assert result["status"] == "ok", result
    assert result["auto"] is True and result["app_version"] and result["schema_revision"] == bk.head_revision()
    async with sessionmaker()() as db:
        assert await get_setting(db, "last_auto_backup")
        assert (await get_setting(db, "backup_state"))["failures"] == 0
    await w.backup_round()  # just made one: not due
    assert w._backup_task.done()
    for _ in range(2):
        assert (await bj.run_auto_backup())["status"] == "ok"
    async with sessionmaker()() as db:
        autos = (await db.execute(select(BackupRecord).where(BackupRecord.auto.is_(True)))).scalars().all()
        ok_autos = [b for b in autos if b.status == "ok"]
        assert len(ok_autos) == 2
        assert all((s.backup_dir / b.filename).is_file() for b in ok_autos)
        m = await db.get(BackupRecord, uuid.UUID(manual.json()["id"]))
        assert m is not None and (s.backup_dir / m.filename).is_file()  # manual backups are never pruned
    assert len(list(s.backup_dir.glob("shc-auto-*.shcbak"))) == 2
    await admin.put("/system/backup-settings", {"clear_passphrase": True})


async def test_failed_auto_backup_alerts_backs_off_and_success_resolves(admin):
    w = BW()
    started = datetime.now(UTC)
    await w.record_auto_result({"status": "failed", "error": "Schijf vol."}, {}, started)
    async with sessionmaker()() as db:
        st = await get_setting(db, "backup_state")
        a = (await db.execute(select(Alert).where(Alert.key == bj.ALERT_KEY, Alert.resolved_at.is_(None)))).scalar_one()
    assert st["failures"] == 1 and st["last_error"] == "Schijf vol."
    assert a.title == "Automatische back-up mislukt" and "Schijf vol." in a.detail
    assert not bj.backup_due(datetime.now(UTC), 3, AMS, None, st)
    r = (await admin.get("/system/backup-settings")).json()
    assert r["failures"] == 1 and r["last_error"] == "Schijf vol." and r["retry_after"]
    await w.record_auto_result({"status": "ok", "filename": "x"}, st, started)
    async with sessionmaker()() as db:
        assert (await db.get(Alert, a.id)).resolved_at is not None
        assert (await get_setting(db, "backup_state"))["failures"] == 0


async def test_manual_backup_answers_202_when_it_takes_longer(admin, monkeypatch):
    monkeypatch.setattr(system, "MANUAL_WAIT_S", 0.0)
    r = await admin.post("/system/backups", json={"passphrase": PW, "include_secrets": False})
    assert r.status_code == 202 and r.json()["status"] == "running"
    await asyncio.wait_for(asyncio.gather(*list(system._background)), 120)
    rows = (await admin.get("/system/backups")).json()
    b = next(x for x in rows if x["id"] == r.json()["id"])
    assert b["status"] == "ok" and b["secrets_included"] is False and b["downloadable"]
    d = await admin.get(f"/system/backups/{b['id']}/download")
    assert d.status_code == 200 and d.content.startswith(b"SHCBAK2\n")


# --- addresses ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "ok"),
    [
        ({"lan_url": "http://192.168.1.20:8088"}, True),
        ({"lan_url": "http://nas.local:8088/"}, True),
        ({"lan_url": "https://10.0.0.5"}, True),
        ({"lan_url": "http://huis.voorbeeld.nl"}, False),
        ({"lan_url": "http://127.0.0.1:8088"}, False),
        ({"lan_url": "ftp://192.168.1.2"}, False),
        ({"remote_url": "https://huis.voorbeeld.nl"}, True),
        ({"remote_url": "http://huis.voorbeeld.nl"}, False),
        ({"remote_url": "https://192.168.1.20"}, False),
        ({"remote_url": "https://user:pw@huis.voorbeeld.nl"}, False),
    ],
)
async def test_address_validation(admin, body, ok):
    before = (await admin.get("/system/settings")).json()
    r = await admin.put("/system/settings", body)
    assert (r.status_code == 200) is ok, r.text
    if not ok:
        assert r.json()["detail"]["code"] == "validation_error"
    else:
        key = next(iter(body))
        assert r.json()[key] == body[key].rstrip("/")
    await admin.put("/system/settings", {"lan_url": before["lan_url"], "remote_url": before["remote_url"]})


# --- restore via upload ---------------------------------------------------------------------------


async def _upload(admin, blob: bytes, passphrase: str):
    return await admin.c.post(
        "/api/v1/system/restore/verify",
        files={"file": ("thuis.shcbak", blob, "application/octet-stream")},
        data={"passphrase": passphrase},
        headers=admin.h(),
    )


async def test_upload_too_large_is_refused(admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    r = await _upload(admin, b"x" * 5000, PW)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "payload_too_large"
    assert not list((get_settings().backup_dir / "uploads").glob("*.shcbak"))


async def test_restore_via_upload_verify_apply_and_compare(admin, home, monkeypatch):
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)
    hid = uuid.UUID(home["id"])
    async with sessionmaker()() as db:
        keep = Device(
            home_id=hid,
            provider="gree",
            kind="ac_unit",
            name="Airco test",
            stable_identity=uuid.uuid4().hex,
            capabilities=["hvac_mode", "setpoint", "fan"],
            online=True,
            last_seen=datetime.now(UTC),
        )
        gone = Device(
            home_id=hid,
            provider="hue",
            kind="light_group",
            name="Licht weg",
            stable_identity=uuid.uuid4().hex,
            capabilities=["power"],
            online=True,
            last_seen=datetime.now(UTC),
        )
        db.add_all([keep, gone])
        await db.commit()
        keep_id, gone_id = keep.id, gone.id
    made = await admin.post("/system/backups", json={"passphrase": PW})
    assert made.status_code == 200, made.text
    blob = (get_settings().backup_dir / made.json()["filename"]).read_bytes()

    wrong = await _upload(admin, blob, "verkeerde wachtwoordzin")
    assert wrong.status_code == 422 and "wachtwoordzin" in wrong.json()["detail"]["message"]
    r = await _upload(admin, blob, PW)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["schema_relation"] == "same" and v["restorable"] and v["refusal"] is None
    assert v["current"]["schema_revision"] == bk.head_revision() and v["format_version"] == 2
    assert v["tables"]["homes"] >= 1 and any(h["id"] == str(hid) for h in v["homes"])
    assert any(d["name"] == "Airco test" and d["provider"] == "gree" for d in v["devices"])
    assert v["filename"] == "thuis.shcbak" and "_integration_rows" not in v

    bad = await admin.post(
        "/system/restore/apply", {"upload_id": v["upload_id"], "passphrase": "verkeerde wachtwoordzin"}
    )
    assert bad.status_code == 422
    r = await admin.post("/system/restore/apply", {"upload_id": v["upload_id"], "passphrase": PW, "mode": "same_home"})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    for _ in range(600):
        job = (await admin.c.get(f"/api/v1/system/restore/jobs/{job_id}")).json()
        if job["state"] != "running":
            break
        await asyncio.sleep(0.1)
    assert job["state"] == "done", job
    assert job["report"]["schema_from"] == bk.head_revision() and job["report"]["mode"] == "same_home"
    assert (await admin.c.get("/api/v1/system/restore/jobs/onbekend")).status_code == 404

    # sessions are not part of a backup: log in again
    r = await admin.c.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
    s = Session(admin.c, r.json()["csrf_token"])
    async with sessionmaker()() as db:
        assert await get_setting(db, "maintenance") is False
        assert await get_setting(db, "safe_mode") is True
        # the worker sees the airco again, but without its fan
        d = await db.get(Device, keep_id)
        d.last_seen, d.online, d.capabilities = (
            datetime.now(UTC) + timedelta(seconds=1),
            True,
            ["hvac_mode", "setpoint"],
        )
        await db.commit()
    c = (await s.get("/system/restore/compare", params={"home_id": str(hid)})).json()
    by = {d["id"]: d for d in c["devices"]}
    assert by[str(keep_id)]["status"] == "partial" and by[str(keep_id)]["missing"] == ["fan"]
    assert by[str(gone_id)]["status"] == "unavailable"
    assert c["safe_mode"] is True and c["restored_at"]
    fn = {f["key"]: f for f in c["functions"]}
    assert fn[f"lights:{gone_id}"]["status"] == "unavailable" and fn[f"lights:{gone_id}"]["suggestion"]
    assert set(c["summary"]) == {"compatible", "partial", "unavailable"}
    assert not (get_settings().backup_dir / "uploads" / f"{v['upload_id']}.shcbak").exists()
    assert (await s.post("/system/safe-mode/release")).status_code == 200


async def test_restore_upload_of_newer_backup_is_marked_not_restorable(admin):
    from .test_ws_backup_format import make_v1

    blob = make_v1({"homes": []}, "0099_toekomst")
    r = await _upload(admin, blob, PW)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["schema_relation"] == "newer" and v["restorable"] is False and "nieuwere versie" in v["refusal"]
    a = await admin.post("/system/restore/apply", {"upload_id": v["upload_id"], "passphrase": PW})
    assert a.status_code == 409
    assert (await admin.delete(f"/system/restore/uploads/{v['upload_id']}")).status_code == 204


class _FakeRequest:
    def __init__(self, body: bytes, ctype: str, chunk: int):
        self.headers = {"content-type": ctype}
        self._body = body
        self._chunk = chunk

    def stream(self):
        parts = iter([self._body[i : i + self._chunk] for i in range(0, len(self._body), self._chunk)])

        class _It:
            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(parts)
                except StopIteration:
                    raise StopAsyncIteration from None

        return _It()


@pytest.mark.parametrize("chunk", [1, 7, 64, 100_000])
async def test_multipart_upload_is_streamed_to_disk_whatever_the_chunking(tmp_path, chunk):
    import os

    payload = os.urandom(3000) + b"\r\n--notreally\r\n" + os.urandom(3000)
    b = "XyZb0undary"
    body = (
        (
            f'--{b}\r\nContent-Disposition: form-data; name="passphrase"\r\n\r\n{PW}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="thuis.shcbak"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + payload
        + f"\r\n--{b}--\r\n".encode()
    )
    dest = tmp_path / f"u{chunk}.bin"
    fields = await system._stream_upload(_FakeRequest(body, f"multipart/form-data; boundary={b}", chunk), dest, 10**6)
    assert fields == {"passphrase": PW, "filename": "thuis.shcbak"}
    assert dest.read_bytes() == payload
    raw = tmp_path / f"r{chunk}.bin"
    assert await system._stream_upload(_FakeRequest(payload, "application/octet-stream", chunk), raw, 10**6) == {}
    assert raw.read_bytes() == payload
    with pytest.raises(system._Limit):
        await system._stream_upload(_FakeRequest(body, f"multipart/form-data; boundary={b}", chunk), dest, 1000)
