"""Bedienbare bronpauze. De database houdt de schrijfgrens ook na een procesherstart vast."""
from __future__ import annotations

import fcntl
import json
import os
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from . import backup as bk
from .commissioning import lock_commissioning
from .config import get_settings
from .models import Base
from .store import audit, ensure_server_identity, get_setting, set_setting

PAUSE_KEY = "migration_source_pause"
LEASE = timedelta(hours=1)


def compose_zigbee_mount(mountinfo: str) -> bool:
    # Het vastgelegde Compose-volume ligt naast de bron van /data (SHC_ROOT/app).
    # Alleen een map op de juiste containerlocatie zegt niets over de gekoppelde opslag.
    mounts = {row[4]: row for line in mountinfo.splitlines() if len(row := line.split()) >= 6}
    app, zigbee = mounts.get("/data"), mounts.get("/data/zigbee2mqtt")
    return bool(app and zigbee and app[2] == zigbee[2] and Path(app[3]).name == "app"
                and Path(zigbee[3]) == Path(app[3]).with_name("zigbee2mqtt") and "ro" in zigbee[5].split(","))


def empty_zigbee_store() -> Path:
    cfg = get_settings()
    if cfg.data_dir == Path("/data"):  # de Compose-API krijgt de bronmap alleen-lezen gekoppeld
        expected = cfg.data_dir / "zigbee2mqtt"
        mounted = compose_zigbee_mount(Path("/proc/self/mountinfo").read_text())
    else:  # de supervisor plaatst app en zigbee2mqtt naast elkaar in dezelfde appdata
        expected = cfg.data_dir.parent / "zigbee2mqtt"
        mounted = cfg.data_dir.name == "app" and cfg.zigbee2mqtt_dir == expected
    configured = cfg.zigbee2mqtt_dir or cfg.data_dir / "zigbee2mqtt"
    if not mounted or configured != expected or expected.resolve() != expected or not expected.is_dir():
        raise bk.BackupError("De verwachte Zigbee2MQTT-opslag is niet aangetoond. De overstap blijft geblokkeerd.")
    if any(expected.iterdir()):
        raise bk.BackupError("Voor de Zigbee2MQTT-bestanden ontbreekt een terugzetproef. Laat de bron in gebruik.")
    return expected


def _store_state(path: Path) -> dict:
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino, "mode": stat.S_IMODE(info.st_mode)}


def fence_store(proof: dict, *, release: bool = False) -> None:
    """Zet de echte lege opslag dicht voordat er een overdraagbaar bronbewijs bestaat."""
    cfg = get_settings()
    path = cfg.zigbee2mqtt_dir or cfg.data_dir / "zigbee2mqtt"
    try:
        expected = proof["storage"]
        fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (expected["device"], expected["inode"]):
                raise bk.BackupError("De gekoppelde Zigbee2MQTT-opslag is gewijzigd.")
            mode = stat.S_IMODE(info.st_mode)
            if release:
                if cfg.data_dir != Path("/data") and mode == expected["mode"] & ~0o222 and mode != expected["mode"]:
                    os.fchmod(fd, expected["mode"])
                    os.fsync(fd)
                return
            if cfg.data_dir == Path("/data"):
                # De ro-bind van de API alleen begrenst de aparte Compose-schrijver (uid 1000) niet.
                # Alleen een lege map zonder schrijfrecht voor de dienst mag een bronbewijs dragen.
                if mode & 0o022 or info.st_uid == 1000:
                    raise bk.BackupError("De Zigbee2MQTT-dienst kan de bronopslag nog beschrijven.")
            elif mode & 0o222:
                os.fchmod(fd, mode & ~0o222)
                os.fsync(fd)
            if os.listdir(fd):
                raise bk.BackupError("De Zigbee2MQTT-opslag bevat gegevens. Bewaar de bron voor terugval.")
        finally:
            os.close(fd)
    except (OSError, KeyError) as exc:
        raise bk.BackupError("De schrijfgrens van de Zigbee2MQTT-opslag is niet aangetoond.") from exc


async def login_journal(db) -> None:
    # Buiten de export: aanmelden blijft mogelijk, ook na herstart van de gepauzeerde bron.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('shc-pause-login', 0))"))
    await db.execute(text("CREATE SCHEMA IF NOT EXISTS shc_pause"))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS shc_pause.attempts (
        id uuid PRIMARY KEY, failed_logins integer NOT NULL, locked_until timestamptz)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS shc_pause.events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz DEFAULT now(),
        actor text, action text, target text, detail jsonb)"""))


async def login_event(db, actor, action, target=None, **detail):
    await db.execute(text("""INSERT INTO shc_pause.events (actor, action, target, detail)
        VALUES (:actor, :action, :target, CAST(:detail AS jsonb))"""),
        {"actor": actor, "action": action, "target": target, "detail": json.dumps(detail)})


def check_database_fence(conn):
    from .cli import MigrationError

    guarded = set(conn.scalars(text("""SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND t.tgname='shc_source_pause'
        AND t.tgenabled IN ('O','A') AND t.tgfoid=to_regprocedure('public.shc_source_paused()')""")))
    if guarded != set(Base.metadata.tables) - {"sessions"}:
        raise MigrationError("De schrijfpauze is onvolledig. Bewaar de oorspronkelijke installatie.")


async def pause(db, actor: str) -> dict:
    await lock_commissioning(db)
    from .restore_control import latest_job

    job = await latest_job(db)
    if await get_setting(db, "migration_pending") or (job and job["state"] == "running"):
        raise bk.BackupError("Wacht tot de open herstelopdracht of overstap is afgehandeld.")
    if await get_setting(db, PAUSE_KEY):
        raise bk.BackupError("De bron heeft al een schrijfpauze.")
    server = await db.run_sync(ensure_server_identity)
    started = datetime.now(UTC)
    until = started + LEASE
    proof = {"id": str(uuid.uuid4()), "source_id": server, "started": started.isoformat(), "expires": until.isoformat(),
             "maintenance_before": bool(await get_setting(db, "maintenance", False))}
    # De triggers wachten op eerdere transacties. Houd de volgorde voor alle schrijfpauzes gelijk.
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    await db.execute(text(f"LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE"))
    proof["storage"] = _store_state(empty_zigbee_store())
    if get_settings().data_dir == Path("/data"):
        # De Compose-API kan de gekoppelde map alleen controleren. Weiger vóór de databasepauze.
        fence_store(proof)
    await login_journal(db)
    if await db.scalar(text("SELECT EXISTS (SELECT FROM integrations WHERE provider='zigbee')")):
        raise bk.BackupError("Voor de Zigbee2MQTT-integratie ontbreekt een terugzetproef.")
    await set_setting(db, "maintenance", True)
    await audit(db, actor, "migration_source_paused")
    await db.flush()
    proof["last_reading"] = await db.scalar(text("SELECT max(id) FROM sensor_readings"))
    proof["reading_count"] = await db.scalar(text("SELECT count(*) FROM sensor_readings"))
    await set_setting(db, PAUSE_KEY, proof)
    await db.flush()
    await db.execute(text("""
        CREATE OR REPLACE FUNCTION shc_source_paused() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE='25006', MESSAGE='De bron is gepauzeerd voor de overstap.';
        END $$"""))
    for table in Base.metadata.sorted_tables:
        if table.name != "sessions":
            await db.execute(text(f'''CREATE TRIGGER shc_source_pause BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
                ON "{table.name}" FOR EACH STATEMENT EXECUTE FUNCTION shc_source_paused()'''))
    await db.commit()
    # Leg de oude rechten eerst duurzaam vast, zodat hervatten ze ook na procesverlies kan herstellen.
    # Elke definitieve export neemt dezelfde schrijfgrens opnieuw onder de loep.
    fence_store(proof)
    return {k: v for k, v in proof.items() if k != "storage"}


async def resume(db, actor: str) -> None:
    await lock_commissioning(db)
    proof = await get_setting(db, PAUSE_KEY)
    if not proof:
        raise bk.BackupError("Er is geen bronpauze.")
    if datetime.now(UTC) < datetime.fromisoformat(proof["expires"]):
        raise bk.BackupError("De bron blijft gepauzeerd tot het getoonde tijdstip waarop het overstapbewijs vervalt.")
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    for table in Base.metadata.sorted_tables:
        if table.name != "sessions":
            await db.execute(text(f'DROP TRIGGER shc_source_pause ON "{table.name}"'))
    await db.execute(text("DROP FUNCTION shc_source_paused()"))
    # Na een alleen-lezen herstart vindt de gewone connectorvoorbereiding in deze transactie plaats.
    from .cli import _check_connector_rows, _prepare_connector_worker
    conn = await db.connection()
    await conn.run_sync(_check_connector_rows)
    await _prepare_connector_worker(conn)
    if "storage" in proof:
        fence_store(proof, release=True)
    if await db.scalar(text("SELECT to_regclass('shc_pause.attempts') IS NOT NULL")):
        await db.execute(text("""UPDATE users u SET failed_logins=a.failed_logins, locked_until=a.locked_until
            FROM shc_pause.attempts a WHERE u.id=a.id"""))
        await db.execute(text("""INSERT INTO audit_log (at,actor,action,target,detail)
            SELECT at,actor,action,target,detail FROM shc_pause.events ORDER BY id"""))
        await db.execute(text("DROP TABLE shc_pause.attempts, shc_pause.events"))
    await db.execute(text("DELETE FROM settings WHERE key='migration_source_pause'"))
    await set_setting(db, "maintenance", proof["maintenance_before"])
    await audit(db, actor, "migration_source_resumed")
    await db.commit()


def complete(proof, source_id) -> bool:
    try:
        start = datetime.fromisoformat(proof["started"])
        end = datetime.fromisoformat(proof["expires"])
        return bool(source_id and proof["source_id"] == source_id and proof["zigbee_store"] == "write_fenced_empty"
                    and start <= datetime.now(UTC) < end <= start + LEASE and uuid.UUID(proof["id"]))
    except (ValueError, TypeError, KeyError):
        return False
