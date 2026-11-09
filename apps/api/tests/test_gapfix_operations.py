"""Follow-ups for verbeterlijst 22, 24, 28, 32, 36, 41, 45 and the small items (Zigbee2MQTT secret in a
redacted backup, stack subnets as pairing address, zone-log retention from the app)."""

import ast
import asyncio
import json
import re
import subprocess
import tomllib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api import events
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, BackupRecord, ClimateZone, Device, Experiment, Integration, ScheduleBlock, ZoneLog
from shc_api.store import get_setting, ingest_reading, set_setting

from .ws_devices_support import add_device, add_integration, fetch, new_home

PW = "gap-wachtwoordzin-123"
API_SRC = Path(__file__).resolve().parents[1] / "src" / "shc_api"
REPO = Path(__file__).resolve().parents[3]


def spy_publish(monkeypatch) -> list[tuple[str, object]]:
    got: list[tuple[str, object]] = []
    real = events.publish

    async def spy(db, type_, home_id, data):
        got.append((type_, home_id))
        await real(db, type_, home_id, data)

    monkeypatch.setattr(events, "publish", spy)
    return got


# --- 22: the CLI restore check reports device keys of another master key ------------------------------


def test_cli_verify_prints_the_key_check(capsys):
    from shc_api import cli
    from shc_api.security import SecretBox

    mine = SecretBox(get_settings().resolved_secret_key())
    other = SecretBox("een-andere-hoofdsleutel-0123456789-abcdefghijkl")
    import base64

    rows = [
        {
            "name": "Hue",
            "provider": "hue",
            "credentials_enc": {"$b64": base64.b64encode(other.encrypt({"k": 1})).decode()},
        },
        {
            "name": "P1",
            "provider": "homewizard",
            "credentials_enc": {"$b64": base64.b64encode(mine.encrypt({})).decode()},
        },
        {"name": "Gree", "provider": "gree", "credentials_enc": None},
    ]
    v = SimpleNamespace(
        manifest={
            "created_at": "2026-11-09T03:00:00+00:00",
            "app_version": "0.1.0",
            "schema_revision": bk.head_revision(),
            "tables": {"integrations": {"rows": 3}},
            "zigbee2mqtt_included": True,
        },
        format_version=2,
        summary={"_integration_rows": rows},
    )
    cli._print_manifest(v)
    out = capsys.readouterr().out
    assert "Apparaatsleutels: 1/2 te ontsleutelen" in out
    assert "NIET te ontsleutelen: Hue (hue)" in out
    assert "shc-api rekey --old-key-file" in out
    v.summary = {"_integration_rows": rows[1:]}
    cli._print_manifest(v)
    out = capsys.readouterr().out
    assert "1/1" in out and "rekey" not in out


# --- 24: an older duplicate integration without identity is cleaned up on re-pair ------------------


async def test_repair_disables_unidentified_duplicate_holding_the_lights(admin, monkeypatch):
    from shc_integrations.base import DiscoveredDevice, PairResult

    from .test_ws_devices_api import BRIDGE, _info, _use

    class FakeHue:
        def __init__(self, config, credentials, **kw):
            self.config = dict(config)

        async def pair(self, host, **kw):
            self.config["host"] = host
            dev = DiscoveredDevice("hue", BRIDGE, "hue_bridge", "Hue Bridge", host, model="BSB002")
            return PairResult(BRIDGE, {"application_key": "k2", "bridge_id": BRIDGE}, dev)

        async def close(self):
            pass

    _use(monkeypatch, hue=_info(FakeHue, "Philips Hue"))
    home_id = await new_home(admin)
    # Before the upgrade the bridge was paired twice. The newest integration got the bridge device (and
    # so an identity in migration 0002); the old one still holds the lights and runs in the worker.
    holder = await add_integration(
        home_id, "hue", "Hue (oud)", config={"host": "192.168.1.2"}, creds={"application_key": "k", "bridge_id": BRIDGE}
    )
    newer = await add_integration(home_id, "hue", "Hue", config={"host": "192.168.1.2"}, identity=BRIDGE)
    other_bridge = await add_integration(
        home_id, "hue", "Hue zolder", config={"host": "192.168.1.3"}, creds={"bridge_id": "ecb5fafffe00ffff"}
    )
    no_creds_same_host = await add_integration(home_id, "hue", "Hue mislukt", config={"host": "192.168.1.20"})
    await add_device(
        home_id, "hue_bridge", "Hue Bridge", provider="hue", stable_identity=BRIDGE, integration_id=newer.id
    )
    lamp = await add_device(home_id, "light", "Plafond", provider="hue", integration_id=holder.id)

    r = await admin.post("/integrations/hue/pair", json={"home_id": str(home_id), "host": "192.168.1.20"})
    assert r.status_code == 200, r.text
    assert r.json()["integration_id"] == str(newer.id)
    old = await fetch(Integration, holder.id)
    assert old.enabled is False and old.credentials_enc is None
    assert (await fetch(Device, lamp.id)).integration_id == newer.id
    assert (await fetch(Integration, no_creds_same_host.id)).enabled is False  # same address, no own identity
    assert (await fetch(Integration, other_bridge.id)).enabled is True  # names another bridge: left alone


# --- 28: push outside the app; 36: status.updated when worker or backups change ----------------------


async def test_notify_send_uses_ntfy_headers_and_reports_errors():
    from shc_api import notify

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500 if b"fout" in request.content else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await notify.send("https://ntfy.sh/geheim-123", "Smart Home: test", "hallo", client=c) is None
        assert "HTTP 500" in await notify.send("https://ntfy.sh/geheim-123", "Café", "fout", client=c)
    assert seen[0].headers["Title"] == "Smart Home: test" and seen[0].headers["Priority"] == "high"
    assert seen[0].content == b"hallo"
    assert seen[1].headers["Title"].startswith("=?UTF-8?B?")  # non-ASCII title for ntfy (RFC 2047)
    assert notify.url_problem("ftp://x") and notify.url_problem("https://user:pw@ntfy.sh/x")
    assert notify.url_problem("https://ntfy.sh/topic") is None
    assert notify.url_hint("https://ntfy.sh/geheim-123") == "https://ntfy.sh/…"


async def test_notify_settings_api(admin, monkeypatch):
    from shc_api import notify

    monkeypatch.setattr(get_settings(), "notify_url", None)
    r = await admin.get("/system/notify")
    assert r.status_code == 200 and r.json()["configured"] is False
    r = await admin.put("/system/notify", {"url": "ftp://nee"})
    assert r.status_code == 422
    r = await admin.put("/system/notify", {"url": "https://ntfy.sh/mijn-geheime-topic"})
    assert r.status_code == 200 and r.json() == {
        **r.json(),
        "configured": True,
        "source": "app",
        "url_hint": "https://ntfy.sh/…",
    }
    assert "mijn-geheime-topic" not in r.text
    async with sessionmaker()() as db:
        raw = await get_setting(db, notify.SETTING_KEY)
        assert raw and "mijn-geheime-topic" not in raw  # stored encrypted
    sent = []

    async def fake_send(url, title, message, **kw):
        sent.append((url, title))
        return None

    monkeypatch.setattr(notify, "send", fake_send)
    r = await admin.post("/system/notify/test")
    assert r.json() == {"ok": True, "error": None} and sent == [("https://ntfy.sh/mijn-geheime-topic", sent[0][1])]
    # The environment value applies when nothing is set in the app.
    assert (await admin.put("/system/notify", {"url": None})).json()["configured"] is False
    monkeypatch.setattr(get_settings(), "notify_url", "http://ntfy.lan/shc")
    assert (await admin.get("/system/notify")).json()["source"] == "env"
    assert "notify_url_enc" in bk.SECRET_SETTING_KEYS  # never in a redacted backup; re-encrypted by rekey


async def test_watch_reports_worker_down_and_back(admin, home, monkeypatch):
    from shc_api import notify, watch

    async with sessionmaker()() as db:
        saved = (await get_setting(db, "worker_heartbeat"), await get_setting(db, watch.WATCH_KEY))
        await set_setting(db, "maintenance", False)
        await set_setting(db, "worker_heartbeat", datetime.now(UTC).isoformat())
        await set_setting(
            db, watch.WATCH_KEY, {"worker": "ok", "backup": "ok", "worker_down": False, "backup_problem": False}
        )
        await db.commit()
    backup = {"state": "ok", "detail": "Laatste back-up 2 uur geleden", "auto_failures": 0, "age_h": 2.0}
    monkeypatch.setattr(watch, "backup_check", lambda db, now=None: _value(backup))
    sent: list[tuple[str, str]] = []

    async def fake_send(url, title, message, **kw):
        sent.append((title, message))
        return None

    async def fake_url(db):
        return "https://ntfy.sh/test", "app"

    monkeypatch.setattr(notify, "send", fake_send)
    monkeypatch.setattr(notify, "configured_url", fake_url)
    published = spy_publish(monkeypatch)
    try:
        async with sessionmaker()() as db:
            assert await watch.check_once(db) is None  # nothing changed
        # The worker stops: no heartbeat for 10 minutes.
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())
            await db.commit()
        async with sessionmaker()() as db:
            r = await watch.check_once(db)
        assert r["state"]["worker_down"] is True and "worker" in r["changed"]
        assert [t for t, _ in sent] == ["Smart Home: achtergronddienst reageert niet"]
        assert "10 min" in sent[0][1]
        assert ("status.updated", uuid.UUID(home["id"])) in published
        async with sessionmaker()() as db:
            key = f"worker_heartbeat:{home['id']}"
            assert (
                await db.execute(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))
            ).scalar_one_or_none()
        # Once only.
        async with sessionmaker()() as db:
            assert await watch.check_once(db) is None
        assert len(sent) == 1
        # Back.
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", datetime.now(UTC).isoformat())
            await db.commit()
        published.clear()
        async with sessionmaker()() as db:
            r = await watch.check_once(db)
        assert r["state"]["worker_down"] is False
        assert sent[-1][0] == "Smart Home: achtergronddienst draait weer"
        assert any(t == "status.updated" for t, _ in published)
        # Backups failing: one message, also when the worker is fine.
        backup.update(state="warn", auto_failures=1, detail="Automatische back-up mislukt (1x): schijf vol")
        async with sessionmaker()() as db:
            r = await watch.check_once(db)
        assert r["changed"] == ["backup", "backup_problem"]
        assert sent[-1][0] == "Smart Home: back-ups lopen niet" and "schijf vol" in sent[-1][1]
        backup.update(state="ok", auto_failures=0, detail="Laatste back-up 1 uur geleden")
        async with sessionmaker()() as db:
            await watch.check_once(db)
        assert sent[-1][0] == "Smart Home: back-ups lopen weer" and len(sent) == 4
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", saved[0])
            await set_setting(db, watch.WATCH_KEY, saved[1] or {})
            await db.execute(delete(Alert).where(Alert.key == f"worker_heartbeat:{home['id']}"))
            await db.commit()


async def _value(v):
    return v


async def test_backup_result_publishes_status(admin, home, monkeypatch):
    from shc_api.worker import Worker

    published = spy_publish(monkeypatch)
    w = Worker()
    async with sessionmaker()() as db:
        saved = await get_setting(db, "backup_state")
    try:
        await w.record_auto_result({"status": "failed", "error": "schijf vol"}, {}, datetime.now(UTC))
        assert ("status.updated", uuid.UUID(home["id"])) in published
        published.clear()
        await w.record_auto_result({"status": "ok", "filename": "x.shcbak"}, {}, datetime.now(UTC))
        assert any(t == "status.updated" for t, _ in published)
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "backup_state", saved or {})
            await db.commit()


# --- 32: encrypted daily database dump, weekly full export ------------------------------------------


async def test_db_dump_is_encrypted_verified_and_restorable_with_pg_restore(admin, tmp_path):
    rec = await bk.run_db_dump(PW, auto=False, backup_dir=tmp_path)
    assert rec["status"] == "ok" and rec["kind"] == bk.DUMP_KIND and rec["filename"].endswith(".shcdump")
    path = tmp_path / rec["filename"]
    raw = path.read_bytes()
    assert raw.startswith(bk.MAGIC) and b"PGDMP" not in raw[:4096] and b"password_hash" not in raw
    assert bk.verify_dump(path, PW) > 0
    with pytest.raises(bk.BackupError, match="Ontsleutelen mislukt"):
        bk.verify_dump(path, "verkeerde wachtwoordzin")
    # It is no full export: the restore wizard refuses it with an explanation.
    with pytest.raises(bk.BackupError, match="database-dump"):
        bk.verify_file(path, PW)
    out = tmp_path / "plain.pgdump"
    assert bk.decrypt_dump(path, PW, out) > 0 and out.read_bytes()[:5] == b"PGDMP"
    listing = (
        await asyncio.to_thread(
            subprocess.run, ["pg_restore", "--list", str(out)], capture_output=True, text=True, check=True
        )
    ).stdout
    assert "TABLE public users" in listing and "TABLE public settings" in listing
    # The overview keeps reporting the Zigbee2MQTT data of the last full export, not of the dump.
    from shc_api.views.status import backup_check

    async with sessionmaker()() as db:
        check = await backup_check(db)
        full = (
            await db.execute(
                select(BackupRecord)
                .where(BackupRecord.status == "ok", BackupRecord.kind != bk.DUMP_KIND)
                .order_by(BackupRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        await db.execute(delete(BackupRecord).where(BackupRecord.filename == rec["filename"]))
        await db.commit()
    assert check["age_h"] is not None and check["age_h"] < 0.1
    assert check["zigbee2mqtt_included"] == ((full.detail or {}).get("zigbee2mqtt_included") if full else None)


def test_dump_schedule_daily_dump_weekly_full():
    from shc_api.worker.backup_job import auto_kind

    now = datetime(2026, 11, 9, 3, 5, tzinfo=UTC)
    assert auto_kind(now, "daily_full", now - timedelta(days=1)) == "full"
    assert auto_kind(now, "dump_daily_full_weekly", None) == "full"
    assert auto_kind(now, "dump_daily_full_weekly", now - timedelta(days=1)) == "dump"
    assert auto_kind(now, "dump_daily_full_weekly", now - timedelta(days=6, hours=6)) == "dump"
    assert auto_kind(now, "dump_daily_full_weekly", now - timedelta(days=7)) == "full"


async def test_backup_settings_schedule_and_separate_pruning(admin, tmp_path, monkeypatch):
    from shc_api.worker import backup_job

    r = await admin.put("/system/backup-settings", {"schedule": "dump_daily_full_weekly"})
    assert r.status_code == 200 and r.json()["schedule"] == "dump_daily_full_weekly"
    assert r.json()["next_kind"] in ("full", "dump")
    assert (await admin.put("/system/backup-settings", {"schedule": "elk-uur"})).status_code == 422
    try:
        monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
        names = []
        async with sessionmaker()() as db:
            for i, kind in enumerate(["db_dump", "db_dump", "db_dump", "export", "export"]):
                name = f"gap-prune-{i}-{kind}"
                (tmp_path / name).write_text("x")
                names.append(name)
                db.add(
                    BackupRecord(
                        kind=kind,
                        filename=name,
                        status="ok",
                        auto=True,
                        created_at=datetime.now(UTC) - timedelta(days=30 - i),
                    )
                )
            await db.commit()
        removed = await backup_job.prune_auto_backups(1, get_settings(), kind=bk.DUMP_KIND)
        assert set(removed) >= {names[0], names[1]} and names[2] not in removed
        assert not (tmp_path / names[0]).exists() and (tmp_path / names[3]).exists()
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(BackupRecord).where(BackupRecord.filename.like("gap-prune-%")))
            await db.commit()
        await admin.put("/system/backup-settings", {"schedule": "daily_full"})


# --- Kleine punten: Zigbee2MQTT secret.yaml in a redacted backup ----------------------------------------


async def test_redacted_backup_blanks_zigbee2mqtt_secrets(admin, tmp_path):
    z = tmp_path / "zigbee2mqtt"
    z.mkdir()
    (z / "configuration.yaml").write_text("mqtt:\n  password: '!secret.yaml mqtt_password'\n")
    (z / "secret.yaml").write_text('mqtt_password: "s3cret-mqtt"\nnetwork_key: [1, 2, 3]\n')

    async def members(include_secrets: bool) -> tuple[dict, dict]:
        target = tmp_path / f"b-{include_secrets}.shcbak"
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as c:
                manifest = await bk.write_backup(c, target, PW, include_secrets, z2m_dir=z)
        finally:
            await engine.dispose()
        files = {n: f.read() for n, f in bk.iter_members(target, PW) if n.startswith("zigbee2mqtt/")}
        return manifest, files

    manifest, files = await members(False)
    secret = files["zigbee2mqtt/secret.yaml"].decode()
    assert "s3cret" not in secret and "1, 2, 3" not in secret
    assert 'mqtt_password: ""' in secret and 'network_key: ""' in secret
    assert manifest["zigbee2mqtt"]["redacted"] == ["secret.yaml"] and manifest["zigbee2mqtt_included"] is True
    assert "zonder wachtwoorden" in manifest["zigbee2mqtt"]["detail"]
    assert b"s3cret" not in (tmp_path / "b-False.shcbak").read_bytes()
    bk.verify_file(tmp_path / "b-False.shcbak", PW)  # checksums match the redacted content
    manifest, files = await members(True)
    assert b"s3cret-mqtt" in files["zigbee2mqtt/secret.yaml"] and manifest["zigbee2mqtt"]["redacted"] == []


# --- Kleine punten: the stack's own Docker subnets are no device address -----------------------------


def test_validate_host_refuses_the_stacks_own_subnets(monkeypatch):
    from shc_api.routers.devices import validate_host

    s = get_settings()
    monkeypatch.setattr(s, "net_prefix", None)
    monkeypatch.setattr(s, "forwarded_allow_ips", "172.30.251.2")
    for ip in ("172.30.250.2", "172.30.251.3", "172.30.252.10", "172.30.252.31"):
        with pytest.raises(ValueError, match="hoort bij de app zelf"):
            validate_host(ip)
    assert validate_host("172.30.253.1") == "172.30.253.1"
    assert validate_host("172.30.252.32") == "172.30.252.32"  # just outside .252.0/27
    monkeypatch.setattr(s, "pairing_allowed_hosts", "172.30.251.3")
    assert validate_host("172.30.251.3") == "172.30.251.3"  # explicitly allowed (test setup)
    monkeypatch.setattr(s, "pairing_allowed_hosts", "")
    monkeypatch.setattr(s, "forwarded_allow_ips", "10.20.251.2")  # SHC_NET_PREFIX=10.20
    with pytest.raises(ValueError):
        validate_host("10.20.250.3")
    assert validate_host("172.30.251.3") == "172.30.251.3"
    monkeypatch.setattr(s, "net_prefix", "172.31")
    with pytest.raises(ValueError):
        validate_host("172.31.252.5")


# --- Kleine punten: the zone log is kept as long as set in the app -----------------------------------


async def test_zone_log_retention_follows_the_app_setting(admin):
    from shc_api.worker import Worker

    home_id = await new_home(admin)
    async with sessionmaker()() as db:
        zid = (await db.execute(select(ClimateZone.id).where(ClimateZone.home_id == home_id))).scalars().first()
        db.add(
            ZoneLog(
                zone_id=zid, kind="engine", message="gap: 100 dagen oud", at=datetime.now(UTC) - timedelta(days=100)
            )
        )
        await db.commit()
    try:
        assert (await admin.put("/system/retention", {"log_days": 365})).status_code == 200
        w = Worker()
        await w.climate_round({home_id})
        await w.retention_round()

        async def count():
            async with sessionmaker()() as db:
                rows = await db.execute(select(ZoneLog).where(ZoneLog.message == "gap: 100 dagen oud"))
                return len(rows.all())

        assert await count() == 1  # the env default (90 days) no longer applies anywhere
        assert (await admin.put("/system/retention", {"log_days": 30})).status_code == 200
        await w.retention_round()
        assert await count() == 0
    finally:
        await admin.put("/system/retention", {"log_days": None})


# --- 45: experiment profiles are switched off again ------------------------------------------------------


async def _zone_state(zid) -> tuple:
    async with sessionmaker()() as db:
        z = await db.get(ClimateZone, zid)
        blocks = sorted(
            (b.start, b.target_c, b.mode, tuple(b.days))
            for b in (await db.execute(select(ScheduleBlock).where(ScheduleBlock.zone_id == zid))).scalars()
        )
        return z.profile_id, z.profile, (z.policy or {}).get("band_c"), tuple(blocks)


async def test_experiment_restores_the_zone_between_variants_after_finish_and_on_stop(admin):
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

    own, a, b = await profile("Eigen", 21, 0.7), await profile("A", 19), await profile("B", 23)
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
    r = await admin.post(
        f"/homes/{hid}/experiments",
        json={"name": "Wissel", "zone_id": str(zid), "auto_apply": True, "variants": variants},
    )
    eid = uuid.UUID(r.json()["id"])
    w = Worker()

    async def step(at):
        await w._experiments_round(at, {hid})
        return await fetch(Experiment, eid)

    e = await step(t0 + timedelta(minutes=10))
    assert e.current_variant == 0 and (await _zone_state(zid))[0] == uuid.UUID(a)
    assert e.result["zone_before"]["profile_id"] == own
    e = await step(t0 + timedelta(hours=1, minutes=30))  # between the variants
    assert e.current_variant is None and await _zone_state(zid) == before
    e = await step(t0 + timedelta(hours=2, minutes=10))
    assert e.current_variant == 1 and (await _zone_state(zid))[0] == uuid.UUID(b)
    e = await step(t0 + timedelta(hours=3, minutes=10))
    assert e.status == "finished" and await _zone_state(zid) == before
    async with sessionmaker()() as db:
        lines = [
            z.message
            for z in (await db.execute(select(ZoneLog).where(ZoneLog.zone_id == zid).order_by(ZoneLog.id))).scalars()
        ]
    assert any("van vóór het experiment teruggezet (Eigen)" in m for m in lines)

    # Stopped while a variant is on: the zone gets its own profile back (default) ...
    async def running_experiment():
        r = await admin.post(
            f"/homes/{hid}/experiments",
            json={
                "name": f"Stop {uuid.uuid4().hex[:4]}",
                "zone_id": str(zid),
                "auto_apply": True,
                "variants": variants,
            },
        )
        eid = uuid.UUID(r.json()["id"])
        await w._experiments_round(t0 + timedelta(minutes=10), {hid})
        assert (await _zone_state(zid))[0] == uuid.UUID(a)
        return eid

    eid = await running_experiment()
    r = await admin.post(f"/experiments/{eid}/stop")
    assert r.json()["status"] == "stopped" and r.json()["zone_restored"] == "restored"
    assert await _zone_state(zid) == before
    # ... or keeps the variant's profile when asked ...
    eid = await running_experiment()
    r = await admin.post(f"/experiments/{eid}/stop?restore=false")
    assert r.json()["zone_restored"] == "kept" and (await _zone_state(zid))[0] == uuid.UUID(a)
    assert (await admin.post(f"/zones/{zid}/profile", json={"profile_id": own})).status_code == 200
    # ... and a profile chosen by hand meanwhile is never overwritten.
    eid = await running_experiment()
    assert (await admin.post(f"/zones/{zid}/profile", json={"profile_id": b})).status_code == 200
    r = await admin.post(f"/experiments/{eid}/stop")
    assert r.json()["zone_restored"] == "changed" and (await _zone_state(zid))[0] == uuid.UUID(b)
    # Deleting a running experiment lets go of the zone too.
    assert (await admin.post(f"/zones/{zid}/profile", json={"profile_id": own})).status_code == 200
    eid = await running_experiment()
    assert (await admin.delete(f"/experiments/{eid}")).status_code == 204
    assert await _zone_state(zid) == before


# --- 36: every realtime message type is listed once, published, and documented consistently ---------


def _published_types() -> dict[str, list[str]]:
    """Literal message types in events.publish(...) calls and {"type": ...} payloads in the source."""
    found: dict[str, list[str]] = {}
    for path in API_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            literal = None
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if name == "publish" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    literal = node.args[1].value
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=False):
                    if isinstance(k, ast.Constant) and k.value == "type" and isinstance(v, ast.Constant):
                        literal = v.value
            if isinstance(literal, str):
                found.setdefault(literal, []).append(path.name)
    return found


def test_every_listed_message_type_is_published_and_nothing_else():
    published = _published_types()
    listed = set(events.MESSAGE_TYPES)
    missing = listed - set(published)
    assert not missing, f"listed in events.MESSAGE_TYPES but never published: {sorted(missing)}"
    # Payload dicts with a "type" key that are not messages (e.g. the client's auth frame) are fine
    # as long as they do not look like a message type (domain.verb).
    unlisted = {t for t in published if re.fullmatch(r"[a-z]+\.[a-z_]+", t)} - listed - set(events.INTERNAL_TYPES)
    assert not unlisted, f"published but not in events.MESSAGE_TYPES: {sorted(unlisted)}"
    # status.updated has more than one source now: integrations, backups, the API's worker watch, CLI.
    assert {"backup_job.py", "status.py"} <= set(published["status.updated"]) | {
        p.name for p in API_SRC.rglob("*.py") if "publish_status(" in p.read_text()
    }


def test_documented_message_types_exist():
    text = (REPO / "docs" / "api.md").read_text()
    section = text.split("## Realtime", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"`([a-z]+\.[a-z_]+)`", section))
    assert documented, "docs/api.md lists no realtime message types"
    unknown = documented - set(events.MESSAGE_TYPES) - set(events.INTERNAL_TYPES)
    assert not unknown, f"docs/api.md names message types the server never sends: {sorted(unknown)}"


async def test_status_updated_is_really_emitted_for_a_backup_change(admin, home):
    """Scenario check next to the static one: a real NOTIFY with the overview status arrives."""
    from shc_api.worker import Worker

    from .test_ws_followups import notifications, wait_for

    async with sessionmaker()() as db:
        saved = await get_setting(db, "backup_state")
    try:
        async with notifications() as got:
            await Worker().record_auto_result({"status": "failed", "error": "test"}, {}, datetime.now(UTC))
            msg = await wait_for(lambda: [m for m in got if m["type"] == "status.updated"])
        backup_row = [c for c in msg[0]["data"] if c["key"] == "backup"][0]
        assert backup_row["auto_failures"] >= 1 and "test" in backup_row["detail"]
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "backup_state", saved or {})
            await db.commit()


# --- 41: hashed lockfile matches the dependency ranges ----------------------------------------------


def test_requirements_lock_pins_every_runtime_dependency_with_hashes():
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    api = REPO / "apps" / "api"
    # A name Dependabot and GitHub's dependency graph recognise (requirements.txt next to its .in),
    # compiled from apps/api with paths relative to it, so Dependabot can rerun the header command.
    assert not (api / "requirements.lock").exists(), "the lock is apps/api/requirements.txt"
    sources = [
        line.strip()
        for line in (api / "requirements.in").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert sources == [".", "../../integrations"]
    lock = (api / "requirements.txt").read_text()
    header = lock.split("\n", 3)[:2]
    assert header[0] == "# This file was autogenerated by uv via the following command:"
    assert header[1].startswith("#    uv pip compile requirements.in ") and header[1].endswith(" -o requirements.txt")
    assert "--generate-hashes" in header[1] and "apps/api/" not in header[1]
    pins: dict[str, str] = {}
    for m in re.finditer(
        r"^([A-Za-z0-9_.-]+)==([^ ;\\\n]+)[^\n]*\\\n((?:\s+--hash=sha256:[0-9a-f]{64}\s*\\?\n?)+)", lock, re.M
    ):
        pins[canonicalize_name(m.group(1))] = m.group(2)
    entries = re.findall(r"^[A-Za-z0-9_.-]+==", lock, re.M)
    assert pins and len(pins) == len(entries), "every pinned package needs --hash lines"
    for project in ("apps/api", "integrations"):
        meta = tomllib.loads((REPO / project / "pyproject.toml").read_text())["project"]
        assert meta["version"] == "0.0.0", f"{project}: the version lives in VERSION only"
        for dep in meta["dependencies"]:
            req = Requirement(dep)
            name = canonicalize_name(req.name)
            if name == "shc-integrations":
                continue
            assert name in pins, f"{project}: {req.name} missing from requirements.txt (regenerate it)"
            assert req.specifier.contains(pins[name], prereleases=True), (
                f"{project}: {req.name}=={pins[name]} in the lock does not satisfy {req.specifier}; regenerate"
            )
    assert "shc-api" not in pins and "shc-integrations" not in pins
    assert json.loads(json.dumps(pins))  # plain data


async def test_cli_dump_and_decrypt_dump(admin, tmp_path, monkeypatch, capsys):
    from shc_api import cli

    args = cli.build_parser().parse_args(["backup", "--auto", "--dump", "--json"])
    assert args.auto and args.dump
    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
    args = cli.build_parser().parse_args(["backup", "--dump", "--json"])
    assert await cli._backup(args) == 0
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["kind"] == bk.DUMP_KIND and rec["auto"] is False
    out = tmp_path / "terug.pgdump"
    args = cli.build_parser().parse_args(["decrypt-dump", str(tmp_path / rec["filename"]), "--out", str(out)])
    assert await cli._decrypt_dump(args) == 0
    assert out.read_bytes()[:5] == b"PGDMP" and (out.stat().st_mode & 0o777) == 0o600
    assert "pg_restore --clean --if-exists" in capsys.readouterr().out
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", "verkeerde wachtwoordzin")
    assert await cli._decrypt_dump(args) == 2
    async with sessionmaker()() as db:
        await db.execute(delete(BackupRecord).where(BackupRecord.filename == rec["filename"]))
        await db.commit()


async def test_status_watch_task_runs_rounds_and_survives_errors(monkeypatch):
    from shc_api import watch

    calls = []

    async def fake_check(db, now=None):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("database even weg")
        return None

    monkeypatch.setattr(watch, "check_once", fake_check)
    w = watch.StatusWatch(interval_s=0.02, first_after_s=0.01)
    w.start()
    await wait_until(lambda: len(calls) >= 3)
    await w.stop()
    n = len(calls)
    await asyncio.sleep(0.05)
    assert len(calls) == n  # stopped
    assert watch.FIRST_AFTER_S >= watch.WORKER_ALERT_S  # first round only after the worker had time to start


async def wait_until(pred, limit_s: float = 3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while not pred():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)
