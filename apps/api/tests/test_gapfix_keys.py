"""Follow-ups for verbeterlijst 22, 32 and 45 found after the second wave.

- Encrypted settings (backup passphrase, push address) from a backup made with another master key
  (restore on a new NAS, key change): AES-GCM raises InvalidTag, which is no ValueError. Nothing may
  crash on it: not the worker's backup loop (the watchdog would restart the worker every ~51 min),
  not the backup process, not the status watch, not the Systeem pages. The admin gets an alert and
  can set the passphrase again in the app.
- The key check before a restore also checks those encrypted settings.
- A paused auto-apply experiment lets go of its variant profile when the variant's time is over and
  is finished after the last variant.
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, ClimateZone, Experiment, ZoneLog
from shc_api.security import SecretBox
from shc_api.store import SecretSettingError, get_secret_setting, get_setting, ingest_reading, set_setting
from shc_api.worker import backup_job as bj

from .test_gapfix_operations import _zone_state, spy_publish
from .ws_devices_support import fetch

PW = "sleutel-wachtwoordzin-123"
OTHER_KEY = "de-hoofdsleutel-van-de-oude-nas-0123456789-abcdef"
SECRET_KEYS = ("backup_passphrase_enc", "notify_url_enc")


def foreign_blob(value: str) -> str:
    """A setting value as the old NAS stored it: SecretBox-encrypted with another master key."""
    return base64.b64encode(SecretBox(OTHER_KEY).encrypt({"v": value})).decode()


def own_blob(value: str) -> str:
    return base64.b64encode(SecretBox(get_settings().resolved_secret_key()).encrypt({"v": value})).decode()


@pytest.fixture
async def saved_settings():
    keys = (*SECRET_KEYS, "backup_state", "last_auto_backup", "last_auto_dump", "status_watch", "worker_heartbeat")
    async with sessionmaker()() as db:
        before = {k: await get_setting(db, k) for k in keys}
    yield
    async with sessionmaker()() as db:
        for k, v in before.items():
            await set_setting(db, k, v)
        await db.execute(delete(Alert).where(Alert.key == bj.ALERT_KEY))
        await db.commit()


# --- 22 / 32: a setting encrypted with another master key ---------------------------------------------


async def test_get_secret_setting_turns_every_decrypt_failure_into_value_error(admin, saved_settings):
    async with sessionmaker()() as db:
        for raw in (foreign_blob("geheim"), "geen base64!", base64.b64encode(b"\x01kort").decode(), "AAAA"):
            await set_setting(db, "backup_passphrase_enc", raw)
            with pytest.raises(ValueError) as exc:
                await get_secret_setting(db, "backup_passphrase_enc")
            assert isinstance(exc.value, SecretSettingError)
            assert "geheim" not in str(exc.value)
        await set_setting(db, "backup_passphrase_enc", own_blob("eigen sleutel"))
        assert await get_secret_setting(db, "backup_passphrase_enc") == "eigen sleutel"
        await db.rollback()


async def test_foreign_key_settings_alert_instead_of_crashing_worker_api_and_watch(
    admin, home, saved_settings, monkeypatch
):
    from shc_api import notify, watch
    from shc_api.worker import Worker

    monkeypatch.setattr(get_settings(), "notify_url", None)
    async with sessionmaker()() as db:
        await set_setting(db, "backup_passphrase_enc", foreign_blob("oude wachtwoordzin"))
        await set_setting(db, "notify_url_enc", foreign_blob("https://ntfy.sh/oud-onderwerp"))
        await set_setting(db, "backup_state", {})
        await set_setting(db, "last_auto_backup", None)
        await set_setting(db, "last_auto_dump", None)
        await db.execute(delete(Alert).where(Alert.key == bj.ALERT_KEY))
        await db.commit()

    # The Systeem pages answer (they used to give 500), with the reason, so the passphrase can be set again.
    r = await admin.get("/system/backup-settings")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["passphrase_error"] and "hoofdsleutel" in s["passphrase_error"]
    assert s["passphrase_set"] is False and s["enabled"] is False and s["wanted"] is True
    r = await admin.get("/system/notify")
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False and "hoofdsleutel" in r.json()["error"]
    assert (await admin.post("/system/notify/test")).status_code == 409  # nothing usable is set

    # The worker's backup loop does not raise (the watchdog would end the worker) but raises the alert.
    w = Worker()
    await w.backup_round()
    await w.backup_round()
    assert getattr(w, "_backup_task", None) is None  # no backup started without a usable passphrase
    async with sessionmaker()() as db:
        alerts = (
            (await db.execute(select(Alert).where(Alert.key == bj.ALERT_KEY, Alert.resolved_at.is_(None))))
            .scalars()
            .all()
        )
    assert len(alerts) == 1 and alerts[0].title == "Automatische back-up kan niet"
    assert "Stel de wachtwoordzin opnieuw in" in alerts[0].detail

    # The backup process itself reports a failure instead of crashing.
    out = await bj.run_auto_backup()
    assert out["status"] == "failed" and "hoofdsleutel" in out["error"]

    # The status watch gets past the push address (it used to raise before its commit).
    async with sessionmaker()() as db:
        await set_setting(db, "maintenance", False)
        await set_setting(db, "worker_heartbeat", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())
        await set_setting(
            db, watch.WATCH_KEY, {"worker": "ok", "backup": "ok", "worker_down": False, "backup_problem": False}
        )
        await db.commit()
    sent = []

    async def fake_send(url, *a, **kw):
        sent.append(url)

    monkeypatch.setattr(notify, "send", fake_send)
    published = spy_publish(monkeypatch)
    async with sessionmaker()() as db:
        r = await watch.check_once(db)
    assert r is not None and r["state"]["worker_down"] is True and r["notified"] == []
    assert sent == []  # the undecryptable address is not used (and no env address is set)
    assert any(t == "status.updated" for t, _ in published)
    async with sessionmaker()() as db:
        assert (await get_setting(db, watch.WATCH_KEY))["worker_down"] is True  # committed
        await db.execute(delete(Alert).where(Alert.key == f"worker_heartbeat:{home['id']}"))
        await db.commit()

    # The admin sets the passphrase again in the app: usable again, and the alert goes away.
    r = await admin.put("/system/backup-settings", {"passphrase": PW})
    assert r.status_code == 200 and r.json()["passphrase_error"] is None and r.json()["enabled"] is True
    async with sessionmaker()() as db:
        await set_setting(db, "last_auto_backup", datetime.now(UTC).isoformat())  # not due: no backup process
        await db.commit()
    await w.backup_round()
    assert getattr(w, "_backup_task", None) is None
    async with sessionmaker()() as db:
        open_ = (
            await db.execute(select(Alert).where(Alert.key == bj.ALERT_KEY, Alert.resolved_at.is_(None)))
        ).scalar_one_or_none()
    assert open_ is None
    assert (await admin.put("/system/notify", {"url": "https://ntfy.sh/nieuw-onderwerp"})).json()["error"] is None


# --- 22: the key check before a restore includes the encrypted settings ---------------------------------


async def test_restore_key_check_reports_encrypted_settings_of_another_key(admin, saved_settings, tmp_path, capsys):
    from shc_api import cli
    from shc_api.routers import system

    async with sessionmaker()() as db:
        await set_setting(db, "backup_passphrase_enc", foreign_blob("oude wachtwoordzin"))
        await set_setting(db, "notify_url_enc", own_blob("https://ntfy.sh/eigen"))
        await db.commit()
    target = tmp_path / "andere-sleutel.shcbak"
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            await bk.write_backup(c, target, PW, True)
    finally:
        await engine.dispose()

    v = bk.verify_file(target, PW)
    assert {r["key"] for r in v.summary["_secret_settings"]} == set(SECRET_KEYS)
    check = bk.decrypt_check(v.summary["_integration_rows"], settings=v.summary["_secret_settings"])
    assert check["settings"] == {"backup_passphrase_enc": "failed", "notify_url_enc": "ok"}
    assert check["settings_failed"] == ["backup_passphrase_enc"]
    warning = bk.key_check_warning(check)
    assert "wachtwoordzin voor automatische back-ups" in warning and "push-adres" not in warning
    assert "shc-api rekey --old-key-file" in warning

    # The web upload (POST /system/restore/verify) warns before anything is changed ...
    r = await admin.c.post(
        "/api/v1/system/restore/verify",
        files={"file": ("andere-sleutel.shcbak", target.read_bytes(), "application/octet-stream")},
        data={"passphrase": PW},
        headers=admin.h(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert warning in body["warnings"]
    assert body["credentials"]["settings_failed"] == ["backup_passphrase_enc"]
    assert "_secret_settings" not in body and "oude wachtwoordzin" not in r.text
    assert (await admin.delete(f"/system/restore/uploads/{body['upload_id']}")).status_code == 204
    # ... and so does `shc-api verify`.
    cli._print_key_check(v)
    out = capsys.readouterr().out
    assert "wachtwoordzin voor automatische back-ups: NIET te ontsleutelen" in out
    assert "push-adres voor meldingen buiten de app: te ontsleutelen" in out
    assert warning in out

    # Everything with this server's key: no warning.
    async with sessionmaker()() as db:
        await set_setting(db, "backup_passphrase_enc", own_blob("eigen wachtwoordzin"))
        await db.commit()
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            await bk.write_backup(c, target, PW, True)
    finally:
        await engine.dispose()
    v = bk.verify_file(target, PW)
    check = bk.decrypt_check(v.summary["_integration_rows"], settings=v.summary["_secret_settings"])
    assert check["settings_failed"] == [] and bk.key_check_warning(check) is None
    summary = system.verify_summary("x", target, v, "x.shcbak")
    assert not any("hoofdsleutel" in w and "wachtwoordzin" in w for w in summary["warnings"])
    # A redacted backup holds no encrypted settings at all.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            await bk.write_backup(c, target, PW, False)
    finally:
        await engine.dispose()
    assert bk.verify_file(target, PW).summary["_secret_settings"] == []


# --- 45: a paused experiment does not keep its variant profile on for good ------------------------------


async def test_paused_experiment_lets_go_of_the_profile_and_finishes(admin):
    from shc_api.worker import Worker

    from .test_ws_climate_worker import make_rig

    rig = await make_rig(admin, living=("heat", 20.0), sensor=True)
    hid, zid = rig.home_id, rig.zones["Woonkamer"]

    async def profile(name, target, band=0.5):
        r = await admin.post(
            f"/homes/{hid}/profiles", json={"name": name, "target_c": target, "mode": "heat", "band_c": band}
        )
        assert r.status_code == 200, r.text
        return r.json()["id"]

    own, a, b = await profile("Eigen P", 21, 0.7), await profile("A P", 19), await profile("B P", 23)
    assert (await admin.post(f"/zones/{zid}/profile", json={"profile_id": own})).status_code == 200
    before = await _zone_state(zid)
    await rig.set_zone("Woonkamer", policy={"stale_after_s": 10**8, "band_c": 0.7})
    async with sessionmaker()() as db:
        await ingest_reading(db, rig.units["sensor:Woonkamer"], "temperature", 20.5, "°C", datetime.now(UTC))
        await db.commit()
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    variants = [
        {"label": "Koel", "start": t0.isoformat(), "end": (t0 + timedelta(hours=1)).isoformat(), "profile_id": a},
        {
            "label": "Warm",
            "start": (t0 + timedelta(hours=2)).isoformat(),
            "end": (t0 + timedelta(hours=3)).isoformat(),
            "profile_id": b,
        },
    ]
    w = Worker()

    async def new_experiment() -> uuid.UUID:
        r = await admin.post(
            f"/homes/{hid}/experiments",
            json={
                "name": f"Pauze {uuid.uuid4().hex[:4]}",
                "zone_id": str(zid),
                "auto_apply": True,
                "variants": variants,
            },
        )
        assert r.status_code == 200, r.text
        eid = uuid.UUID(r.json()["id"])
        await w._experiments_round(t0 + timedelta(minutes=10), {hid})
        assert (await _zone_state(zid))[0] == uuid.UUID(a)
        return eid

    async def step(eid, at):
        await w._experiments_round(at, {hid})
        return await fetch(Experiment, eid)

    # Paused by a user during the first variant: the variant stays while its time lasts ...
    eid = await new_experiment()
    assert (await admin.post(f"/experiments/{eid}/pause")).status_code == 200
    e = await step(eid, t0 + timedelta(minutes=30))
    assert e.status == "paused" and e.current_variant == 0 and (await _zone_state(zid))[0] == uuid.UUID(a)
    # ... but goes off when it is over (also while still paused, and the next variant is not applied) ...
    e = await step(eid, t0 + timedelta(hours=2, minutes=10))
    assert e.status == "paused" and e.current_variant is None and await _zone_state(zid) == before
    # ... and after the last variant the experiment is finished.
    e = await step(eid, t0 + timedelta(hours=3, minutes=10))
    assert e.status == "finished" and await _zone_state(zid) == before

    # Paused by the worker (the zone was operated by hand) and never resumed: after the last variant the
    # zone gets its own profile back and the "gepauzeerd" alert goes away.
    eid = await new_experiment()
    await rig.set_zone("Woonkamer", control="manual")
    e = await step(eid, t0 + timedelta(minutes=20))
    assert e.status == "paused" and "handmatig" in e.paused_reason
    async with sessionmaker()() as db:
        assert (
            await db.execute(select(Alert).where(Alert.key == f"experiment:{eid}", Alert.resolved_at.is_(None)))
        ).scalar_one_or_none()
    assert (await _zone_state(zid))[0] == uuid.UUID(a)
    e = await step(eid, t0 + timedelta(hours=3, minutes=10))
    assert e.status == "finished" and await _zone_state(zid) == before
    async with sessionmaker()() as db:
        z = await db.get(ClimateZone, zid)
        assert z.control == "manual"  # the hand control stays; only the profile goes back
        assert (
            await db.execute(select(Alert).where(Alert.key == f"experiment:{eid}", Alert.resolved_at.is_(None)))
        ).scalar_one_or_none() is None
        lines = [
            m
            for m in (await db.execute(select(ZoneLog.message).where(ZoneLog.zone_id == zid))).scalars()
            if "afgerond (gepauzeerd)" in m
        ]
    assert len(lines) == 2
    await rig.set_zone("Woonkamer", control="schedule")
    # A finished experiment is left alone.
    assert (await step(eid, t0 + timedelta(hours=4))).status == "finished"
