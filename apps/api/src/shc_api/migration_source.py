"""Bedienbare bronpauze. De database houdt de schrijfgrens ook na een procesherstart vast."""
from __future__ import annotations

import fcntl
import json
import os
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import inspect, text

from . import backup as bk
from . import migration_inventory
from .commissioning import lock_commissioning
from .config import get_settings
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


def fence_store(proof: dict, *, release: bool = False, restore_on_error: bool = False) -> None:
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
            try:
                if cfg.data_dir != Path("/data") and mode & 0o222:
                    os.fchmod(fd, mode & ~0o222)
                    os.fsync(fd)
                if os.listdir(fd):
                    raise bk.BackupError("De Zigbee2MQTT-opslag bevat gegevens. Bewaar de bron voor terugval.")
            except (OSError, bk.BackupError):
                # Gebruik hetzelfde geopende object, ook als de koppeling ondertussen is verplaatst.
                if (restore_on_error and cfg.data_dir != Path("/data")
                        and expected["mode"] != stat.S_IMODE(os.fstat(fd).st_mode)):
                    os.fchmod(fd, expected["mode"])
                    os.fsync(fd)
                raise
        finally:
            os.close(fd)
    except (OSError, KeyError) as exc:
        raise bk.BackupError("De schrijfgrens van de Zigbee2MQTT-opslag is niet aangetoond.") from exc


async def login_journal(db) -> None:
    # Dit journaal blijft beschikbaar tot de beheerder de bronregistratie sluit.
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


def _database_tables(conn):
    quote = conn.dialect.identifier_preparer.format_table
    return {key: quote(t) for key, t in migration_inventory.catalog(conn).items() if key != "alembic_version"}


def check_database_fence(conn):
    from .cli import MigrationError

    guarded = set(conn.scalars(text("""SELECT CASE WHEN n.nspname='public' THEN c.relname::text
        ELSE n.nspname || '.' || c.relname END FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.tgname='shc_source_pause'
        AND t.tgenabled IN ('O','A') AND t.tgfoid=to_regprocedure('public.shc_source_paused()')""")))
    if guarded != set(_database_tables(conn)) - {"sessions"}:
        raise MigrationError("De schrijfpauze is onvolledig. Bewaar de oorspronkelijke installatie.")
    stopped = conn.scalar(text("SELECT value->'v'->>'stopped_at' FROM settings WHERE key='migration_source_pause'"))
    if stopped:
        closed = set(conn.scalars(text("""SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='shc_pause'
            AND t.tgname='shc_registrations_closed' AND t.tgenabled IN ('O','A')
            AND t.tgfoid=to_regprocedure('shc_pause.registrations_closed()')""")))
        if closed != {"attempts", "events"}:
            raise MigrationError("De afsluiting van het aanmeldjournaal is onvolledig.")


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
             "maintenance_before": bool(await get_setting(db, "maintenance", False)), "stopped_at": None}
    # De triggers wachten op eerdere transacties. Houd de volgorde voor alle schrijfpauzes gelijk.
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    tables = await (await db.connection()).run_sync(_database_tables)
    await db.execute(text(f"LOCK TABLE {', '.join(tables.values())} IN SHARE ROW EXCLUSIVE MODE"))
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
    for name, table in tables.items():
        if name != "sessions":
            await db.execute(text(f'''CREATE TRIGGER shc_source_pause BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
                ON {table} FOR EACH STATEMENT EXECUTE FUNCTION shc_source_paused()'''))
    await db.commit()
    # Leg de oude rechten eerst duurzaam vast, zodat hervatten ze ook na procesverlies kan herstellen.
    # Hervatten en exporteren kunnen tussen de transacties de lock al hebben verkregen.
    await lock_commissioning(db)
    current = await get_setting(db, PAUSE_KEY)
    if not current or current["id"] != proof["id"]:
        raise bk.BackupError("De bronpauze is inmiddels hervat.")
    try:
        fence_store(proof, restore_on_error=not current.get("stopped_at"))
    except bk.BackupError:
        # De oude rechten zijn al duurzaam vastgelegd. Maak uitsluitend onze eigen
        # geweigerde pauze ongedaan zolang die geen definitief bewijs heeft uitgegeven.
        await resume(db, actor, rejected_id=proof["id"])
        raise
    await db.commit()
    return {k: v for k, v in current.items() if k != "storage"}


async def _merge_logins(db):
    # Verplaatsen en verwijderen vormen één transactie, ook bij een herhaalde export of terugval.
    # Laat een later, uitgebreid journaal intact als deze versie zijn velden niet kan overdragen.
    conn = await db.connection()
    journal = await conn.run_sync(lambda c: inspect(c).get_multi_columns(
        schema="shc_pause", filter_names=["attempts", "events"]))
    for name, columns in (("attempts", {"id", "failed_logins", "locked_until"}),
                          ("events", {"id", "at", "actor", "action", "target", "detail"})):
        if {c["name"] for c in journal.get(("shc_pause", name), ())} != columns:
            raise bk.BackupError("Het aanmeldjournaal bevat onbekende kolommen. Bewaar de bron voor terugval.")
    await db.execute(text("""WITH moved AS (DELETE FROM shc_pause.attempts RETURNING *)
        UPDATE users u SET failed_logins=m.failed_logins, locked_until=m.locked_until
        FROM moved m WHERE u.id=m.id"""))
    await db.execute(text("""WITH moved AS (DELETE FROM shc_pause.events RETURNING *)
        INSERT INTO audit_log (at,actor,action,target,detail)
        SELECT at,actor,action,target,detail FROM moved ORDER BY id"""))


def logins_closed(proof):
    return bool(proof and proof.get("stopped_at") and datetime.now(UTC) < datetime.fromisoformat(proof["expires"]))


async def prepare_export(db, actor: str, *, stop: bool):
    # Aanmeldingen houden de gedeelde installatielock tot en met hun commit. De export
    # wacht ook op een aanmelding waarvan de wachtwoordcontrole nog loopt.
    await lock_commissioning(db)
    proof = await get_setting(db, PAUSE_KEY)
    if not proof or "stopped_at" not in proof or datetime.now(UTC) >= datetime.fromisoformat(proof["expires"]):
        raise bk.BackupError("Begin een nieuwe bronpauze voor de export. Hervat een eerdere pauze na de vervaltijd.")
    if proof["stopped_at"]:
        return
    await db.execute(text("LOCK TABLE shc_pause.attempts, shc_pause.events IN ACCESS EXCLUSIVE MODE"))
    # PostgreSQL houdt de tabelsloten tot commit. Andere verbindingen zien de triggers steeds ingeschakeld.
    for name in ("users", "audit_log", "settings"):
        await db.execute(text(f'ALTER TABLE "{name}" DISABLE TRIGGER shc_source_pause'))
    await _merge_logins(db)
    if stop:
        await set_setting(db, PAUSE_KEY, {**proof, "stopped_at": datetime.now(UTC).isoformat()})
        await audit(db, actor, "migration_source_stopped")
        await db.flush()
        # Ook reeds geopende verbindingen en oudere bronprocessen moeten de grens respecteren.
        await db.execute(text("""CREATE FUNCTION shc_pause.registrations_closed() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF clock_timestamp() >= (SELECT (value->'v'->>'expires')::timestamptz
                        FROM public.settings WHERE key='migration_source_pause') THEN RETURN NULL; END IF;
                RAISE EXCEPTION USING ERRCODE='25006', MESSAGE='De bronregistratie is afgesloten.';
            END $$"""))
        for name in ("attempts", "events"):
            await db.execute(text(f"""CREATE TRIGGER shc_registrations_closed
                BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
                ON shc_pause.{name} FOR EACH STATEMENT EXECUTE FUNCTION shc_pause.registrations_closed()"""))
    for name in ("users", "audit_log", "settings"):
        await db.execute(text(f'ALTER TABLE "{name}" ENABLE TRIGGER shc_source_pause'))


async def resume(db, actor: str, *, rejected_id: str | None = None) -> None:
    await lock_commissioning(db)
    proof = await get_setting(db, PAUSE_KEY)
    if rejected_id and (not proof or proof["id"] != rejected_id):
        return
    if not proof:
        raise bk.BackupError("Er is geen bronpauze.")
    # Een pauze uit een oude versie kan al een bewijs hebben uitgegeven.
    if proof.get("stopped_at", True) and datetime.now(UTC) < datetime.fromisoformat(proof["expires"]):
        raise bk.BackupError("De bron blijft gepauzeerd tot het getoonde tijdstip waarop het overstapbewijs vervalt.")
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    for name, table in (await (await db.connection()).run_sync(_database_tables)).items():
        if name != "sessions":
            await db.execute(text(f"DROP TRIGGER IF EXISTS shc_source_pause ON {table}"))
    await db.execute(text("DROP FUNCTION shc_source_paused()"))
    if not rejected_id:
        # Na een alleen-lezen herstart vindt de gewone connectorvoorbereiding in deze transactie plaats.
        from .cli import _check_connector_rows, _prepare_connector_worker
        conn = await db.connection()
        await conn.run_sync(_check_connector_rows)
        await _prepare_connector_worker(conn)
        if "storage" in proof:
            fence_store(proof, release=True)
    if await db.scalar(text("SELECT to_regclass('shc_pause.attempts') IS NOT NULL")):
        if proof.get("stopped_at"):
            for name in ("attempts", "events"):
                await db.execute(text(f"DROP TRIGGER shc_registrations_closed ON shc_pause.{name}"))
            await db.execute(text("DROP FUNCTION shc_pause.registrations_closed()"))
        await _merge_logins(db)
        await db.execute(text("DROP TABLE shc_pause.attempts, shc_pause.events"))
    await db.execute(text("DELETE FROM settings WHERE key='migration_source_pause'"))
    await set_setting(db, "maintenance", proof["maintenance_before"])
    await audit(db, actor, "migration_source_resumed")
    await db.commit()


def complete(proof, source_id) -> bool:
    try:
        start = datetime.fromisoformat(proof["started"])
        end = datetime.fromisoformat(proof["expires"])
        stopped = datetime.fromisoformat(proof["stopped_at"])
        return bool(source_id and proof["source_id"] == source_id and proof["zigbee_store"] == "write_fenced_empty"
                    and start <= stopped <= datetime.now(UTC) < end <= start + LEASE and uuid.UUID(proof["id"]))
    except (ValueError, TypeError, KeyError):
        return False
