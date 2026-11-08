"""Backup stream: restore across schema versions, refusal of newer backups, pre-migration dump,
migration errors, restore drill, rekey, reset-password and the worker check (verbeterlijst 34, 22)."""

import argparse
import asyncio
import base64
import gzip
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import ApiClient, Integration
from shc_api.security import SecretBox
from shc_api.store import get_setting, set_secret_setting, set_setting

from .test_ws_backup_format import PW, make_v1, temp_db


async def _scalar(url: str, sql: str, **params):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            return (await c.execute(text(sql), params)).scalar()
    finally:
        await engine.dispose()


def _old_backup(schema: str) -> bytes:
    hid = str(uuid.uuid4())
    return make_v1(
        {
            "homes": [
                {
                    "id": hid,
                    "name": "Oud huis",
                    "timezone": "Europe/Amsterdam",
                    "currency": "EUR",
                    "active_from": "2025-01-01T00:00:00+00:00",
                    "active_to": None,
                    "legacy_flag": True,  # a column this version does not know
                }
            ],
            "tariffs": [
                {
                    "id": str(uuid.uuid4()),
                    "home_id": hid,
                    "valid_from": "2025-01-01",
                    "import_per_kwh": "0.30000",
                    "export_per_kwh": "0.05000",
                    "fixed_per_day": "0.90000",
                    "source": "handmatig",
                },
                {  # duplicate start date: migration 0002 keeps one
                    "id": str(uuid.uuid4()),
                    "home_id": hid,
                    "valid_from": "2025-01-01",
                    "import_per_kwh": "0.31000",
                    "export_per_kwh": "0.05000",
                    "fixed_per_day": "0.90000",
                    "source": "handmatig",
                },
            ],
            "old_table": [{"x": 1}, {"x": 2}],
        },
        schema,
    )


async def test_older_backup_is_restored_in_its_schema_then_migrated_and_drops_are_listed(tmp_path):
    p = tmp_path / "oud.shcbak"
    p.write_bytes(_old_backup("0001"))
    assert bk.schema_relation("0001") == "older"
    async with temp_db() as url:
        report = await bk.restore_file(p, PW, url=url)
        r = report.as_dict()
        assert r["schema_from"] == "0001" and r["schema_to"] == bk.head_revision() and r["migrated"]
        assert r["counts"]["homes"] == 1 and r["counts"]["tariffs"] == 2
        assert r["dropped"]["columns"] == {"homes": ["legacy_flag"]}
        assert r["dropped"]["tables"] == {"old_table": 2}
        assert r["dropped"]["rows_removed_by_migration"] == {"tariffs": 1}
        assert await _scalar(url, "SELECT version_num FROM alembic_version") == bk.head_revision()
        assert await _scalar(url, "SELECT count(*) FROM tariffs") == 1
        assert await _scalar(url, "SELECT value->>'v' FROM settings WHERE key = 'safe_mode'") == "true"
        assert await _scalar(url, "SELECT value->>'v' FROM settings WHERE key = 'maintenance'") == "false"
        # the new columns of 0002 got their defaults
        assert await _scalar(url, "SELECT count(*) FROM backups") == 0


async def test_backup_of_newer_schema_is_refused(tmp_path):
    p = tmp_path / "nieuw.shcbak"
    p.write_bytes(_old_backup("0099_toekomst"))
    v = bk.verify_file(p, PW)
    assert bk.schema_relation(v.manifest["schema_revision"]) == "newer"
    with pytest.raises(bk.BackupError, match="nieuwere versie"):
        bk.check_restorable(v.manifest)
    with pytest.raises(bk.BackupError, match="nieuwere versie"):
        await bk.restore_file(p, PW, url="postgresql+asyncpg://nooit@127.0.0.1:1/x")


async def test_pre_migration_dump_and_refusal_when_dump_fails(tmp_path, monkeypatch):
    async with temp_db("0001") as url:
        dump = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
        assert dump is not None and dump.name.startswith("pre-migrate-0001-") and dump.name.endswith(".sql.gz")
        assert b"CREATE TABLE" in gzip.decompress(dump.read_bytes())
        assert cli.current_revision(url) == bk.head_revision()
        assert await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path) is None  # nothing pending: no dump
    async with temp_db("0001") as url:
        fake = tmp_path / "pg_dump"
        fake.write_text("#!/bin/sh\necho 'verbinding geweigerd' >&2\nexit 1\n")
        fake.chmod(0o755)
        monkeypatch.setattr(cli.shutil, "which", lambda _n: str(fake))
        with pytest.raises(cli.MigrationError, match="NIET bijgewerkt") as ei:
            await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path / "x")
        assert "verbinding geweigerd" in str(ei.value)
        assert cli.current_revision(url) == "0001"
        assert not list((tmp_path / "x").glob("*.sql.gz"))


async def test_migration_errors_propagate_from_env_inside_a_running_loop():
    from alembic import command

    bad = "postgresql+asyncpg://postgres@127.0.0.1:55432/shc_bestaat_niet"
    with pytest.raises(Exception, match="shc_bestaat_niet"):
        command.upgrade(bk.alembic_config(bad), "head")  # runs in a helper thread; error must come back


async def _integration_with_creds(url: str, key: str, creds: dict) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as c:
        hid = uuid.uuid4()
        await c.execute(
            text("INSERT INTO homes (id, name, timezone, currency, active_from) VALUES (:h, 'R', 'UTC', 'EUR', now())"),
            {"h": hid},
        )
        await c.execute(
            text(
                "INSERT INTO integrations (id, home_id, provider, name, config, credentials_enc, state, detail, enabled,"
                " created_at) VALUES (:i, :h, 'hue', 'Bridge', '{}', :c, 'ok', '', true, now())"
            ),
            {"i": uuid.uuid4(), "h": hid, "c": SecretBox(key).encrypt(creds)},
        )
    await engine.dispose()


async def test_restore_drill_compares_counts_and_checks_the_current_key(admin, tmp_path, monkeypatch):
    key = get_settings().resolved_secret_key()
    async with temp_db() as url:
        await _integration_with_creds(url, key, {"app_key": "geheim"})
        rec = await bk.run_backup(PW, True, url=url, backup_dir=tmp_path)
    path = tmp_path / rec["filename"]
    r = await cli.restore_drill(path, PW)
    assert r["ok"] is True, r
    assert r["credentials"] == {"ok": 1, "failed": [], "total": 1}
    assert r["tables"]["integrations"] == {"backup": 1, "restored": 1, "now": 1, "ok": True}
    # the drill never touched the real database and dropped its temporary one
    n = await _scalar(
        get_settings().database_url, "SELECT count(*) FROM pg_database WHERE datname = :n", n=r["database"]
    )
    assert n == 0
    monkeypatch.setattr(get_settings(), "secret_key", "een-heel-andere-hoofdsleutel-0123456789-abcdef")
    r = await cli.restore_drill(path, PW)
    assert r["ok"] is False and r["credentials"]["failed"] == ["Bridge (hue)"]


async def test_rekey_reencrypts_credentials_and_secret_settings(monkeypatch):
    old_key = get_settings().resolved_secret_key()
    new_key = "nieuwe-hoofdsleutel-voor-de-test-0123456789-xyz"
    async with temp_db() as url:
        await _integration_with_creds(url, old_key, {"token": "abc"})
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as c:
            blob = base64.b64encode(SecretBox(old_key).encrypt({"v": "wachtwoordzin backup"})).decode()
            await c.execute(
                text("INSERT INTO settings (key, value) VALUES ('backup_passphrase_enc', CAST(:v AS jsonb))"),
                {"v": json.dumps({"v": blob})},
            )
        monkeypatch.setattr(get_settings(), "secret_key", new_key)
        dry = await cli.rekey(old_key, dry_run=True, url=url)
        assert dry["rekeyed"] == ["Bridge (hue)"]
        out = await cli.rekey(old_key, url=url)
        assert out["rekeyed"] == ["Bridge (hue)"] and out["settings"] == {"backup_passphrase_enc": "rekeyed"}
        again = await cli.rekey(old_key, url=url)
        assert again["already_current"] == ["Bridge (hue)"] and not again["rekeyed"]
        async with engine.connect() as c:
            enc = (await c.execute(text("SELECT credentials_enc FROM integrations"))).scalar_one()
            raw = (
                await c.execute(text("SELECT value->>'v' FROM settings WHERE key='backup_passphrase_enc'"))
            ).scalar_one()
        await engine.dispose()
    box = SecretBox(new_key)
    assert box.decrypt(enc) == {"token": "abc"}
    assert box.decrypt(base64.b64decode(raw)) == {"v": "wachtwoordzin backup"}


async def test_reset_password_revokes_phone_tokens(admin, app):
    name = f"reset{uuid.uuid4().hex[:6]}"
    r = await admin.post("/auth/users", json={"username": name, "password": "oud-wachtwoord-123", "role": "admin"})
    assert r.status_code in (200, 201), r.text
    async with sessionmaker()() as db:
        from shc_api.models import User

        uid = (await db.execute(select(User.id).where(User.username == name))).scalar_one()
        db.add(ApiClient(user_id=uid, name="Telefoon", token_hash=uuid.uuid4().hex * 2, role="control"))
        await db.commit()
    out = await cli.reset_password(name, "nieuw-wachtwoord-456")
    assert out["phones_revoked"] == 1
    async with sessionmaker()() as db:
        c = (await db.execute(select(ApiClient).where(ApiClient.user_id == uid))).scalar_one()
        assert c.revoked_at is not None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cl:
        ok = await cl.post("/api/v1/auth/login", json={"username": name, "password": "nieuw-wachtwoord-456"})
        assert ok.status_code == 200


async def test_cli_restore_refused_while_worker_runs_unless_maintenance(admin, tmp_path, monkeypatch):
    rec = await bk.run_backup(PW, True, backup_dir=tmp_path)
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
    args = argparse.Namespace(
        file=str(tmp_path / rec["filename"]),
        passphrase_file=None,
        passphrase_stdin=False,
        z2m_dir=None,
        yes=True,
        mode="same_home",
    )
    async with sessionmaker()() as db:
        before = await get_setting(db, "worker_heartbeat")
        await set_setting(db, "worker_heartbeat", datetime.now(UTC).isoformat())
        await set_setting(db, "maintenance", False)
        await db.commit()
    try:
        assert await cli._restore(args, dry_run=False) == 3
        assert await cli._restore(args, dry_run=True) == 0  # checking is always allowed
        assert await cli.worker_running() == (True, False)
        await cli.set_maintenance(True)
        assert await cli.worker_running() == (True, True)
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", before)
            await set_setting(db, "maintenance", False)
            await db.commit()


async def test_archive_home_switches_its_integrations_off(admin):
    r = await admin.post("/homes", json={"name": f"Verhuisd {uuid.uuid4().hex[:4]}"})
    hid = uuid.UUID(r.json()["id"])
    async with sessionmaker()() as db:
        i = Integration(home_id=hid, provider="hue", name="Oude bridge", config={}, enabled=True)
        db.add(i)
        await db.commit()
        iid = i.id
    r = await admin.post(f"/homes/{hid}/archive")
    assert r.status_code == 200 and r.json()["active"] is False
    async with sessionmaker()() as db:
        assert (await db.get(Integration, iid)).enabled is False


async def test_secret_settings_are_encrypted_at_rest(admin):
    async with sessionmaker()() as db:
        await set_secret_setting(db, "backup_passphrase_enc", "mijn geheime zin 123")
        await db.commit()
        raw = await get_setting(db, "backup_passphrase_enc")
        assert "geheime" not in raw
        await set_secret_setting(db, "backup_passphrase_enc", None)
        await db.commit()
        assert await get_setting(db, "backup_passphrase_enc") is None


async def test_restore_as_new_home_archives_the_old_home_and_switches_its_integrations_off(tmp_path):
    from shc_api.routers.homes import DEFAULT_ROLES

    key = get_settings().resolved_secret_key()
    async with temp_db() as url:
        await _integration_with_creds(url, key, {"a": 1})
        rec = await bk.run_backup(PW, True, url=url, backup_dir=tmp_path)
        r = (await bk.restore_file(tmp_path / rec["filename"], PW, url=url, mode="new_home")).as_dict()
        new = uuid.UUID(r["new_home_id"])
        assert r["mode"] == "new_home" and len(r["archived_homes"]) == 1
        assert await _scalar(url, "SELECT name FROM homes WHERE active_to IS NULL") == "R (nieuw)"
        assert await _scalar(url, "SELECT count(*) FROM homes WHERE active_to IS NOT NULL") == 1
        assert await _scalar(url, "SELECT bool_or(enabled) FROM integrations") is False
        assert await _scalar(url, "SELECT count(*) FROM logical_roles WHERE home_id = :h", h=new) == len(DEFAULT_ROLES)
        assert await _scalar(url, "SELECT count(*) FROM climate_zones WHERE home_id = :h", h=new) == 2
        assert await _scalar(url, "SELECT value->>'v' FROM settings WHERE key = 'safe_mode'") == "true"
