"""Vergelijking vóór vervanging en een lokaal taakjournaal, buiten de teruggezette tabellen."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from . import backup as bk
from .config import get_settings
from .migration_inventory import catalog, fingerprint
from .models import Base


def _encoded(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rollback_dir() -> Path:
    path = get_settings().backup_dir / "terugval"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def sync_directory(path: Path) -> None:
    directory = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def state_stamp(conn) -> str:
    """Bind ook wijzigingen met gelijke aantallen. xmin verandert bij iedere nieuwe rijversie."""
    known = set(Base.metadata.tables) | {"alembic_version"}
    actual = catalog(conn)
    if not known.issubset(actual):
        raise bk.BackupError("De doelback-up kan niet alle databasetabellen verantwoorden.")
    revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != bk.head_revision():
        raise bk.BackupError("Werk het doelschema bij vóór de vergelijking.")
    digest = hashlib.sha256(revision.encode())
    for name, table in sorted(actual.items()):
        if name in {"alembic_version", "sessions", "processed_events"}:
            continue
        digest.update(json.dumps([name, sorted(table.columns.keys())]).encode() + b"\0")
        if not len(table.primary_key):
            digest.update(_encoded(fingerprint(conn, table)))
            continue
        stmt = select(*table.primary_key, text("xmin::text")).order_by(*table.primary_key)
        if name == "settings":
            # Vluchtige dienstinformatie en de herstelpauze veranderen terwijl de beheerder vergelijkt.
            stmt = stmt.where(table.c.key.not_in(("maintenance", "worker_heartbeat", "worker_loops", "clock_check",
                                                   "status_watch")))
        page = stmt
        while rows := conn.execute(page.limit(2000)).all():
            for row in rows:
                digest.update(json.dumps(list(row), default=str).encode() + b"\n")
            page = stmt.where(tuple_(*table.primary_key) > tuple_(*rows[-1][:-1]))
    return digest.hexdigest()


def _table_signature(manifest: dict, name: str):
    table = manifest["tables"].get(name, {})
    return [manifest["files"][f]["sha256"] for f in table.get("files", [])]


async def prepare(path: Path, passphrase: str, mode: str, owner: str, upload: str, source: bk.Verified) -> dict:
    cfg = get_settings()
    key = uuid.uuid4()
    dest = rollback_dir() / f"{key}.shcbak"
    receipt = dest.with_suffix(".json")
    engine = create_async_engine(cfg.database_url, poolclass=NullPool, hide_parameters=True)
    try:
        dest.touch(mode=0o600, exist_ok=False)
        async with engine.connect() as conn:
            stamp = {}
            await bk.write_backup(conn, dest, passphrase, True, cfg.zigbee2mqtt_dir or cfg.data_dir / "zigbee2mqtt",
                                  target_stamp=stamp)
        target = await asyncio.to_thread(bk.verify_file, dest, passphrase)
        if target.manifest["zigbee2mqtt"]["unreadable"]:
            raise bk.BackupError("De terugvalkopie bevat onleesbare Zigbee-opslag.")
        tables = {}
        for name in sorted(target.manifest["tables"].keys() | source.manifest["tables"].keys()):
            tables[name] = {
                "before": target.manifest["tables"].get(name, {}).get("rows", 0),
                "after": source.manifest["tables"].get(name, {}).get("rows", 0),
                "same": _table_signature(target.manifest, name) == _table_signature(source.manifest, name),
            }
        comparison = {"mode": mode, "rollback_id": str(key), "rollback_sha256": await asyncio.to_thread(_sha, dest),
                      "archive_sha256": await asyncio.to_thread(_sha, path), "homes_before": target.summary["homes"],
                      "homes_after": source.summary["homes"], "tables": tables}
        proof = {"comparison": comparison, "owner": owner, "upload": upload, "stamp": stamp["value"],
                 "expires": int(time.time()) + 1800}
        with os.fdopen(os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            json.dump(proof, f)
            f.flush()
            os.fsync(f.fileno())
        sync_directory(dest.parent)
        return {"comparison": comparison, "confirmation": _sign(proof)}
    except BaseException:
        dest.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
        raise
    finally:
        await engine.dispose()


def _sign(proof: dict) -> str:
    return hmac.new(get_settings().resolved_secret_key().encode(), b"restore-preview:v1\0" + _encoded(proof),
                    hashlib.sha256).hexdigest()


def approval(rollback_id: uuid.UUID | None, confirmation: str | None, mode: str, owner: str,
             upload: str, path: Path) -> dict:
    if not rollback_id or not confirmation:
        raise bk.BackupError("Maak eerst een doelback-up en bevestig de getoonde vergelijking.")
    dest = rollback_dir() / f"{rollback_id}.shcbak"
    try:
        proof = json.loads(dest.with_suffix(".json").read_text())
        comp = proof["comparison"]
        valid = (hmac.compare_digest(_sign(proof), confirmation) and proof["owner"] == owner
                 and proof["upload"] == upload and proof["expires"] > time.time() and comp["mode"] == mode
                 and comp["rollback_id"] == str(rollback_id) and _sha(dest) == comp["rollback_sha256"]
                 and _sha(path) == comp["archive_sha256"])
    except (OSError, KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise bk.BackupError("De doelback-up, het archief of het akkoord wijkt af. Vergelijk opnieuw.")
    return proof


def check_guard(conn, proof: dict, path: Path) -> None:
    comp = proof["comparison"]
    dest = rollback_dir() / f"{uuid.UUID(comp['rollback_id'])}.shcbak"
    if (proof["expires"] <= time.time() or not dest.is_file() or _sha(dest) != comp["rollback_sha256"]
            or _sha(path) != comp["archive_sha256"] or state_stamp(conn) != proof["stamp"]):
        raise bk.BackupError("Het doel of de back-up is gewijzigd. Maak een nieuwe vergelijking.")


def job_key(job_id: str) -> str:
    return hashlib.sha256(job_id.encode()).hexdigest()


def job_time() -> str:
    return datetime.now(UTC).isoformat()


async def create_job(db, key: str, job: dict, rollback_id: str) -> None:
    # Een apart schema overleeft TRUNCATE, schema-downgrade en herstel van een oude export.
    await db.execute(text("CREATE SCHEMA IF NOT EXISTS shc_restore"))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS shc_restore.jobs (
        id text PRIMARY KEY, rollback_id uuid NOT NULL UNIQUE, data jsonb NOT NULL)"""))
    if await db.scalar(text("SELECT EXISTS (SELECT FROM shc_restore.jobs WHERE data->>'state' = 'running')")):
        raise bk.BackupError("Er loopt al een herstel.")
    used = text("SELECT EXISTS (SELECT FROM shc_restore.jobs WHERE rollback_id = :id)")
    if await db.scalar(used, {"id": rollback_id}):
        raise bk.BackupError("Dit akkoord is al gebruikt. Vergelijk opnieuw.")
    await db.execute(text("INSERT INTO shc_restore.jobs VALUES (:id, :rollback, CAST(:data AS jsonb))"),
                     {"id": key, "rollback": rollback_id, "data": json.dumps(job)})


async def begin_job(db, key: str, mode: str, rollback_id: str) -> None:
    """Leg de bedrijfsstand en de taak samen met de onderhoudspauze vast."""
    from .store import get_setting, set_setting

    job = {"state": "running", "step": "wachten op de databasevervanging", "mode": mode,
           "started_at": job_time(), "finished_at": None, "report": None, "error": None,
           "_maintenance_before": bool(await get_setting(db, "maintenance", False)),
           "_safe_before": bool(await get_setting(db, "safe_mode", False))}
    await create_job(db, key, job, rollback_id)
    await set_setting(db, "maintenance", True)
    await db.commit()


async def job_record(db, key: str) -> dict | None:
    if not await db.scalar(text("SELECT to_regclass('shc_restore.jobs') IS NOT NULL")):
        return None
    return await db.scalar(text("SELECT data FROM shc_restore.jobs WHERE id = :id"), {"id": key})


async def latest_job(db) -> dict | None:
    if not await db.scalar(text("SELECT to_regclass('shc_restore.jobs') IS NOT NULL")):
        return None
    row = (await db.execute(text("""SELECT rollback_id, data FROM shc_restore.jobs
        ORDER BY data->>'started_at' DESC LIMIT 1"""))).first()
    return ({"rollback_id": str(row.rollback_id), "state": row.data["state"], "step": row.data["step"],
             "started_at": row.data["started_at"]} if row else None)


def finish_job(conn, key: str, **changes) -> None:
    result = conn.execute(text("""UPDATE shc_restore.jobs SET data=data || CAST(:changes AS jsonb)
        WHERE id=:id AND data->>'state'='running'"""),
                          {"id": key, "changes": json.dumps({**changes, "finished_at": job_time()})})
    if result.rowcount != 1:
        raise bk.BackupError("De hersteltaak is onderbroken. Vergelijk opnieuw.")

# Apart van het transactieslot voor inbedrijfstelling. Een procesverlies geeft dit sessieslot vrij.
JOB_LOCK = 730591


async def claim_worker():
    from .db import engine

    conn = await engine().connect()
    try:
        if not await conn.scalar(select(text(f"pg_try_advisory_lock({JOB_LOCK})"))):
            raise bk.BackupError("Er loopt al een herstel.")
        await conn.commit()
        return conn
    except BaseException:
        await conn.close()
        raise


async def release_worker(conn) -> None:
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": JOB_LOCK})
    finally:
        await conn.close()


async def fail_job(db, key, job, step, error):
    from .store import set_setting

    conn = await db.connection()
    await conn.run_sync(finish_job, key, state="failed", step=step, error=error[:500])
    await set_setting(db, "maintenance", job["_maintenance_before"])
    if "_safe_before" in job:
        await set_setting(db, "safe_mode", job["_safe_before"])


async def recover(db) -> None:
    if not await db.scalar(text("SELECT to_regclass('shc_restore.jobs') IS NOT NULL")):
        return
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": JOB_LOCK}):
        return
    result = await db.execute(text("SELECT id, data FROM shc_restore.jobs WHERE data->>'state'='running' FOR UPDATE"))
    for key, job in result:
        await fail_job(db, key, job, "onderbroken",
                       "Onderbroken vóór de databasevervanging. Vergelijk opnieuw om te hervatten.")
    await db.commit()
