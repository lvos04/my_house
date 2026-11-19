"""Bedienbare bronpauze. De database houdt de schrijfgrens ook na een procesherstart vast."""
from __future__ import annotations

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


async def pause(db, actor: str) -> dict:
    await lock_commissioning(db)
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
    empty_zigbee_store()
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
            -- Na het geldigheidsvenster kan men ook met een verlopen sessie opnieuw inloggen voor terugval.
            IF TG_TABLE_NAME IN ('users','audit_log') AND statement_timestamp() >=
                (SELECT (value->'v'->>'expires')::timestamptz FROM settings WHERE key='migration_source_pause')
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION USING ERRCODE='25006', MESSAGE='De bron is gepauzeerd voor de overstap.';
        END $$"""))
    for table in Base.metadata.sorted_tables:
        if table.name != "sessions":
            await db.execute(text(f'''CREATE TRIGGER shc_source_pause BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
                ON "{table.name}" FOR EACH STATEMENT EXECUTE FUNCTION shc_source_paused()'''))
    await db.commit()
    return proof


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
    await db.execute(text("DELETE FROM settings WHERE key='migration_source_pause'"))
    await set_setting(db, "maintenance", proof["maintenance_before"])
    await audit(db, actor, "migration_source_resumed")
    await db.commit()


def complete(proof, source_id) -> bool:
    try:
        start = datetime.fromisoformat(proof["started"])
        end = datetime.fromisoformat(proof["expires"])
        return bool(source_id and proof["source_id"] == source_id and proof["zigbee_store"] == "verified_empty"
                    and start <= datetime.now(UTC) < end <= start + LEASE and uuid.UUID(proof["id"]))
    except (ValueError, TypeError, KeyError):
        return False
