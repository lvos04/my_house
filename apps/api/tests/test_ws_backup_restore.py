"""Backup stream: restore across schema versions, refusal of newer backups, pre-migration dump,
migration errors, restore drill, rekey, reset-password and the worker check (verbeterlijst 34, 22)."""

import argparse
import asyncio
import base64
import gzip
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import ApiClient, Integration, PairingCode, User
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


async def test_migratie_0008_en_terugzetten_behouden_bestaande_telefoons(tmp_path):
    from alembic import command

    gebruiker, telefoon, hub, code = (uuid.uuid4() for _ in range(4))
    aangemaakt = datetime.now(UTC) - timedelta(days=2)
    vervalt = aangemaakt + timedelta(days=180)
    async with temp_db("0007") as url:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as db:
                await db.execute(User.__table__.insert().values(
                    id=gebruiker, username="kijker", role="viewer", password_hash=uuid.uuid4().hex,
                ))
                await db.execute(text(
                    "INSERT INTO api_clients (id, user_id, name, token_hash, role, created_at) "
                    "VALUES (:id, :user, 'Kijktelefoon', :token_hash, 'viewer', :created)"
                ), {"id": telefoon, "user": gebruiker, "created": aangemaakt, "token_hash": uuid.uuid4().hex})
                gebruikersrij = await db.scalar(text("SELECT row_to_json(users) FROM users"))
                telefoonrij = await db.scalar(text("SELECT row_to_json(api_clients) FROM api_clients"))
            p = tmp_path / "schema0007.shcbak"
            p.write_bytes(make_v1({"users": [gebruikersrij], "api_clients": [telefoonrij]}, "0007"))
            dump = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
            assert dump is not None and dump.is_file()
            async with engine.begin() as db:
                assert (await db.execute(select(ApiClient.purpose, ApiClient.role, ApiClient.expires_at))).one() == (
                    "phone", "viewer", vervalt,
                )
                await db.execute(ApiClient.__table__.insert().values(
                    id=hub, user_id=gebruiker, name="Hub", token_hash=uuid.uuid4().hex,
                    role="viewer", purpose="hub", expires_at=vervalt,
                ))
                await db.execute(PairingCode.__table__.insert().values(
                    id=code, user_id=gebruiker, code_hash=uuid.uuid4().hex, role="viewer", purpose="hub",
                    expires_at=vervalt,
                ))
            await asyncio.to_thread(command.downgrade, bk.alembic_config(url), "0007")
            assert await _scalar(url, "SELECT revoked_at IS NOT NULL FROM api_clients WHERE id = :id", id=hub)
            assert await _scalar(url, "SELECT used_at IS NOT NULL FROM pairing_codes WHERE id = :id", id=code)
        finally:
            await engine.dispose()

    async with temp_db() as doel:
        rapport = await bk.restore_file(p, PW, url=doel)
        assert rapport.schema_from == "0007" and rapport.migrated
        assert rapport.counts["users"] == rapport.counts["api_clients"] == 1
        assert await _scalar(doel, "SELECT purpose FROM api_clients WHERE id = :id", id=telefoon) == "phone"
        assert await _scalar(doel, "SELECT expires_at FROM api_clients WHERE id = :id", id=telefoon) == vervalt
        assert await _scalar(doel, "SELECT username FROM users WHERE id = :id", id=gebruiker) == "kijker"


async def test_migration_errors_propagate_from_env_inside_a_running_loop():
    from alembic import command

    from .conftest import PG  # same server as the rest of the suite (local or CI)

    bad = PG.replace("postgresql://", "postgresql+asyncpg://") + "/shc_bestaat_niet"
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


async def test_web_rekey_uses_old_key_without_persisting_it(admin, tmp_path, monkeypatch, caplog):
    import logging
    import secrets

    caplog.set_level(logging.INFO)
    async with sessionmaker()() as db:
        await db.execute(text("SELECT 1"))  # Initialiseer de API-pool vóór de tijdelijke configuratie.
    cfg = get_settings()
    old_key, new_key = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    async with temp_db() as url:
        await _integration_with_creds(url, old_key, {"kenmerk": "proef"})
        # De API-sessie blijft in de eigen testdatabase. Het gedeelde rekey-pad schrijft in de proefdatabase.
        monkeypatch.setattr(cfg, "database_url", url)
        monkeypatch.setattr(cfg, "secret_key", new_key)
        monkeypatch.setattr(cfg, "backup_dir", tmp_path)
        r = await admin.post("/system/restore/rekey", content=old_key.encode())
        assert r.status_code == 200, r.text
        assert r.json()["rekeyed"] == 1
        enc = await _scalar(url, "SELECT credentials_enc FROM integrations")
        assert SecretBox(new_key).decrypt(enc) == {"kenmerk": "proef"}
        assert old_key not in caplog.text and old_key not in r.text
        assert not any(old_key.encode() in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
        r = await admin.post("/system/restore/rekey", content=b"x" * 4097)
        assert r.status_code == 413


async def test_web_restore_drill_reports_sandbox_counts(admin, tmp_path, monkeypatch):
    from .test_ws_backup_api import _upload

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    async with temp_db() as url:
        await _integration_with_creds(url, get_settings().resolved_secret_key(), {"kenmerk": "proef"})
        rec = await bk.run_backup(PW, url=url, backup_dir=tmp_path)
    blob = (tmp_path / rec["filename"]).read_bytes()
    verified = (await _upload(admin, blob, PW)).json()
    r = await admin.post("/system/restore/drill", {"upload_id": verified["upload_id"], "passphrase": PW})
    assert r.status_code == 200, r.text
    assert r.json()["tables"]["integrations"] == {"backup": 1, "restored": 1, "now": 1, "ok": True}
    assert r.json()["credentials"]["total"] == 1
    assert "database" not in r.json()
    newer = (await _upload(admin, _old_backup("0099_toekomst"), PW)).json()
    r = await admin.post("/system/restore/drill", {"upload_id": newer["upload_id"], "passphrase": PW})
    assert r.status_code == 409


async def test_web_migration_preserves_source_and_requires_current_comparison(tmp_path, monkeypatch):
    import secrets

    from shc_api.db import dispose
    from shc_api.main import create_app
    from shc_api.models import Device, Home, SensorReading
    from shc_api.routers import system
    from shc_api.store import ensure_server_identity

    from .conftest import Session
    from .test_ws_backup_api import _upload

    cfg = get_settings()
    passphrase, password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    monkeypatch.setattr(cfg, "backup_dir", tmp_path)
    monkeypatch.setattr(cfg, "secret_key", secrets.token_urlsafe(48))
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    app = create_app()
    async with temp_db() as source, temp_db() as target:
        await dispose()
        try:
            monkeypatch.setattr(cfg, "database_url", source)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/api/v1/auth/setup", json={"username": "bron", "password": password})
                assert r.status_code == 200
                admin = Session(c, r.json()["csrf_token"])
                home = await admin.post("/homes", {"name": "Proefwoning"})
                assert home.status_code == 200
            hid, did = uuid.UUID(home.json()["id"]), uuid.uuid4()
            async with sessionmaker()() as db:
                source_id = await db.run_sync(ensure_server_identity)
                db.add(Device(id=did, home_id=hid, provider="hue", kind="light_group", name="Proeflamp",
                              stable_identity=uuid.uuid4().hex, capabilities=["power"]))
                await db.flush()
                db.add(SensorReading(device_id=did, metric="power", value=2.5, unit="W",
                                     event_id=uuid.uuid4().hex, observed_at=datetime.now(UTC)))
                await db.commit()
            snapshot = """SELECT jsonb_build_object(
                'users', (SELECT jsonb_agg(p) FROM (SELECT id, username, role FROM users) p),
                'homes', (SELECT jsonb_agg(homes) FROM homes),
                'history', (SELECT jsonb_agg(sensor_readings) FROM sensor_readings))"""
            before = await _scalar(source, snapshot)
            backup = await bk.run_backup(passphrase, url=source, backup_dir=tmp_path)
            payload = (tmp_path / backup["filename"]).read_bytes()
            await dispose()
            monkeypatch.setattr(cfg, "database_url", target)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/api/v1/auth/setup", json={"username": "doel", "password": password})
                assert r.status_code == 200
                admin = Session(c, r.json()["csrf_token"])
                checked = (await _upload(admin, payload, passphrase)).json()
                upload = {"upload_id": checked["upload_id"], "passphrase": passphrase}
                apply = {**upload, "mode": "migration"}
                assert (await admin.post("/system/restore/apply", apply)).status_code == 409
                drill = (await admin.post("/system/restore/drill", upload)).json()
                assert drill["data_ok"] and drill["source_id"] == source_id != drill["target_id"]
                apply["drill_id"] = drill["drill_id"]
                async with sessionmaker()() as db:
                    extra = Home(name="Al in gebruik")
                    db.add(extra)
                    await db.commit()
                    extra_id = extra.id
                refused = await admin.post("/system/restore/apply", apply)
                assert refused.status_code == 409 and refused.json()["detail"]["code"] == "migration_target_refused"
                prep = bk.MigrationTarget(drill["target_id"], uuid.UUID(r.json()["user"]["id"]), source_id)
                with pytest.raises(bk.BackupError, match="doel bevat al gegevens"):
                    await bk.restore_file(tmp_path / backup["filename"], passphrase, url=target,
                                          mode="migration", migration=prep)
                assert await _scalar(target, "SELECT name FROM homes") == "Al in gebruik"
                async with sessionmaker()() as db:
                    await db.delete(await db.get(Home, extra_id))
                    await db.commit()
                accepted = await admin.post("/system/restore/apply", apply)
                assert accepted.status_code == 202, accepted.text
                job_id = accepted.json()["job_id"]
                async with asyncio.timeout(30):
                    while (job := (await admin.get(f"/system/restore/jobs/{job_id}")).json())["state"] == "running":
                        await asyncio.sleep(0.05)
                assert job["state"] == "done", job
                assert await _scalar(target, snapshot) == before == await _scalar(source, snapshot)
                r = await c.post("/api/v1/auth/login", json={"username": "bron", "password": password})
                assert r.status_code == 200
                admin = Session(c, r.json()["csrf_token"])
                assert (await admin.post("/system/safe-mode/release")).status_code == 409
                comparison = (await admin.get("/system/restore/compare")).json()
                assert comparison["migration"]["confirmation"] is None
                assert comparison["migration"]["target_id"] == drill["target_id"]
                assert comparison["source_homes"] == comparison["homes"] == {str(hid): "Proefwoning"}
                async with sessionmaker()() as db:
                    device = await db.get(Device, did)
                    device.last_seen, device.online = datetime.now(UTC), True
                    home2 = Home(name="Tweede proefwoning")
                    db.add(home2)
                    await db.flush()
                    duplicate = Device(home_id=home2.id, provider=device.provider, kind=device.kind,
                                       name="Tweede proeflamp", stable_identity=device.stable_identity)
                    db.add(duplicate)
                    await db.commit()
                    dup_id, other_id = duplicate.id, home2.id
                comparison = (await admin.get("/system/restore/compare")).json()
                assert comparison["migration"]["confirmation"] is None
                assert {x["home_name"] for x in comparison["duplicates"][0]["devices"]} == {
                    "Proefwoning", "Tweede proefwoning",
                }
                async with sessionmaker()() as db:
                    await db.delete(await db.get(Device, dup_id))
                    await db.delete(await db.get(Home, other_id))
                    await db.commit()
                comparison = (await admin.get("/system/restore/compare")).json()
                approval = {"confirmation": comparison["migration"]["confirmation"], "source_stopped": True}
                assert approval["confirmation"]
                async with sessionmaker()() as db:
                    (await db.get(Device, did)).name = "Naam na vergelijking"
                    await db.commit()
                assert (await admin.post("/system/restore/confirm", approval)).status_code == 409
                fresh = (await admin.get("/system/restore/compare")).json()["migration"]["confirmation"]
                assert (await admin.post("/system/restore/confirm", {"confirmation": fresh})).status_code == 422
                next_confirmation = (await admin.get("/system/restore/compare")).json()["migration"]["confirmation"]
                assert (await admin.post("/system/restore/confirm", {**approval, "confirmation": fresh})).status_code == 409
                fresh = next_confirmation
                r = await admin.post("/system/restore/confirm", {**approval, "confirmation": fresh})
                assert r.status_code == 200 and r.json() == {"safe_mode": False}
                assert (await admin.post("/system/restore/confirm", {**approval, "confirmation": fresh})).status_code == 409
                assert await _scalar(source, snapshot) == before
            await dispose()
            monkeypatch.setattr(cfg, "database_url", source)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/api/v1/auth/login", json={"username": "bron", "password": password})
                assert r.status_code == 200
                assert [h["id"] for h in (await Session(c, r.json()["csrf_token"]).get("/homes")).json()] == [str(hid)]
        finally:
            await dispose()
