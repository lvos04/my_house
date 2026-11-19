"""Controleer opdracht, richting, vorige stand en herstelstatus binnen dezelfde database."""

import asyncio
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

# Hetzelfde open herstel blokkeert een downgrade en wordt bij logisch herstel eerst veiliggesteld.
IN_FLIGHT = """
    EXISTS (SELECT 1 FROM commands cmd WHERE cmd.id IN (c.command_id, c.rollback_command_id)
            AND cmd.status IN ('sending', 'sent'))
"""
RESTORED = """
    c.rollback_status = 'confirmed' AND c.rollback_after = c.before AND c.rollback_at IS NOT NULL
    AND EXISTS (SELECT 1 FROM commands undo WHERE undo.id = c.rollback_command_id AND undo.status = 'confirmed')
"""
OPEN_RECOVERY = f"""
    c.command_id IS NOT NULL AND (
        {IN_FLIGHT} OR (
            ({RESTORED} AND jsonb_typeof(c.before->'controls') = 'object') IS NOT TRUE
            AND (c.rollback_command_id IS NOT NULL OR c.rollback_status IS NOT NULL
                 OR c.status = 'confirmed' AND (c.after IS DISTINCT FROM c.before OR NOT (c.before ? 'controls'))
                 OR c.reason_code IN ('readback_unavailable', 'readback_unknown_after_restart',
                                      'readback_unknown_after_restore', 'readback_mismatch', 'readback_side_effect',
                                      'readback_incomplete')
                    AND (c.after IS NULL OR c.after IS DISTINCT FROM c.before OR NOT (c.before ? 'controls')))
        )
    )
"""

# Het oude formaat legde alleen voeding, modus en doeltemperatuur vast. Alleen een volledig
# afgehandeld paar mag met precies die historische metingen mee. Dit verleent geen uitvoerrecht.
INCOMPLETE_PROOF = f"""
    WITH proof AS (
        SELECT c.*, (
            {RESTORED} AND c.status = 'confirmed' AND c.rollback_from = c.after
            AND c.before = jsonb_build_object('power', c.before->'power', 'mode', c.before->'mode',
                                               'target_c', c.before->'target_c')
        ) IS TRUE AS historical
        FROM connector_changes c
    ), links AS (
        SELECT c.*, link.cmd_id, link.phase, link.undo
        FROM proof c
        CROSS JOIN LATERAL (
            SELECT c.command_id AS cmd_id, c.status AS phase, false AS undo
            UNION ALL SELECT c.rollback_command_id, c.rollback_status, true
            UNION ALL SELECT attempt::uuid, 'failed', true
                FROM jsonb_array_elements_text(c.rollback_from->'previous_attempts') AS attempt
        ) link
        WHERE link.cmd_id IS NOT NULL
    )
    SELECT EXISTS (
        SELECT 1 FROM commands cmd
        LEFT JOIN links l ON l.cmd_id = cmd.id
        WHERE cmd.source = 'connector'
          AND (cmd.status NOT IN ('cancelled', 'denied') OR l.id IS NOT NULL OR cmd.attempts > 0)
          AND (l.id IS NULL OR (
              l.action_id = 'zone.command' AND cmd.action = 'set_hvac'
              AND l.target->>'zone_id' = cmd.zone_id::text AND l.observed_at IS NOT NULL
              AND jsonb_typeof(l.before->'power') = 'boolean' AND jsonb_typeof(l.before->'mode') = 'string'
              AND jsonb_typeof(l.before->'target_c') = 'number'
              AND (l.historical OR jsonb_typeof(l.before->'controls') = 'object'
                   AND l.before->'controls' @> jsonb_build_object('TemUn', 0))
              AND jsonb_typeof(l.parameters->'target_c') = 'number'
              AND cmd.params = CASE WHEN l.undo THEN jsonb_build_object('target_c', l.before->'target_c')
                                    ELSE l.parameters END
              AND l.phase IN ('queued', 'confirmed', 'failed')
              AND CASE WHEN cmd.status IN ('queued', 'sending', 'sent') THEN l.phase = 'queued'
                       WHEN cmd.status IN ('cancelled', 'denied') THEN l.phase IN ('queued', 'failed')
                       ELSE l.phase = cmd.status END
              AND (l.status <> 'confirmed' OR l.after_at IS NOT NULL AND l.after = l.before || l.parameters)
              AND (l.rollback_status IS DISTINCT FROM 'confirmed'
                   OR l.rollback_at IS NOT NULL AND l.rollback_after = l.before)
              AND (NOT l.undo OR (l.historical OR jsonb_typeof(l.rollback_from->'state'->'target_c') = 'number'
                                   AND (l.rollback_from->'state') - 'target_c' = l.before - 'target_c')
                   AND EXISTS (SELECT 1 FROM commands original WHERE original.id = l.command_id
                               AND original.device_id = cmd.device_id AND original.home_id = cmd.home_id))
          ) IS NOT TRUE)
        UNION ALL
        SELECT 1 FROM links l LEFT JOIN commands cmd ON cmd.id = l.cmd_id AND cmd.source = 'connector'
        WHERE cmd.id IS NULL
        UNION ALL
        SELECT 1 FROM links GROUP BY cmd_id HAVING count(*) <> 1
    )
"""  # noqa: S608 - RESTORED is vaste broncode


@asynccontextmanager
async def _proof_database(url: str):
    from .cli import _libpq

    dsn, password = _libpq(url)
    name = f"shc_proof_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn, password=password, database="postgres")
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        try:
            db = await asyncpg.connect(dsn, password=password, database=name)
            try:
                yield db
            finally:
                await db.close()
        finally:
            await admin.execute(f'DROP DATABASE "{name}"')
    finally:
        await admin.close()


async def check_archive(archive: Path, url: str) -> None:
    """Controleer alleen data in een eigen PostgreSQL-database. Voer geen SQL uit het archief uit."""
    from .backup import BackupError
    from .models import Base

    pg_restore = shutil.which("pg_restore")
    if not pg_restore:
        raise BackupError("pg_restore ontbreekt. Het connectorherstelbewijs kan niet worden gecontroleerd.")
    tables = {name: Base.metadata.tables[name] for name in ("commands", "connector_changes", "settings")}
    async with _proof_database(url) as db:
        for name, model in tables.items():
            # Geen functies, triggers of rechten uit het archief. De tabellen bevatten alleen onze kolommen en typen.
            table = Table(name, MetaData(), *(Column(col.name, col.type) for col in model.columns))
            await db.execute(str(CreateTable(table).compile(dialect=postgresql.dialect())))
        proc = await asyncio.create_subprocess_exec(
            pg_restore, "--data-only", "--schema=public", *(f"--table={t}" for t in tables),
            "--file=-", str(archive), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=64 << 20,
        )
        try:
            await _load_copy_data(proc.stdout, db, tables)
            if await proc.wait():
                raise ValueError("Onvolledige database-dump")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.communicate()
        # Op 0006 zit dezelfde geschiedenis tijdelijk in de gewone instellingentabel.
        await db.execute("""
            INSERT INTO connector_changes
            SELECT (jsonb_populate_record(NULL::connector_changes, value->'v')).* FROM settings
            WHERE value->>'format' = 'connector-0007' AND key = 'connector_history:' || (value->'v'->>'id')
        """)
        if await db.fetchval(INCOMPLETE_PROOF):
            raise BackupError("Terugzetten geweigerd: het connectorherstelbewijs ontbreekt of is onvolledig.")


async def _load_copy_data(stream, db, tables):
    seen = set()
    while line := await stream.readline():
        if not line.startswith(b"COPY "):
            continue
        match = re.fullmatch(rb'COPY public\.(\w+) \(([\w", ]+)\) FROM stdin;\n', line)
        if not match:
            raise ValueError("Onleesbare COPY-kop")
        name = match[1].decode()
        if name not in tables or name in seen:
            raise ValueError("Onverwachte gegevenstabel")
        fields = [part.strip('"') for part in match[2].decode().split(", ")]
        if not set(fields).issubset(tables[name].columns.keys()):
            raise ValueError("Onbekende kolommen")
        seen.add(name)
        await db.copy_to_table(name, columns=fields, source=_copy_rows(stream, name, fields))
    if "commands" not in seen:
        raise ValueError("De opdrachten ontbreken in de database-dump")


async def _copy_rows(stream, table_name, fields):
    # PostgreSQL COPY-tekst ontsnapt tabs en regeleinden in veldwaarden. Laat PostgreSQL de waarden decoderen.
    index = fields.index({"settings": "key", "commands": "source"}.get(table_name, "id"))
    while line := await stream.readline():
        if line == b"\\.\n":
            return
        if table_name != "connector_changes":
            field = line.rstrip(b"\n").split(b"\t")[index]
            if table_name == "commands" and field != b"connector":
                continue
            if table_name == "settings" and not field.startswith(b"connector_history:"):
                continue
        yield line
    raise ValueError("De COPY-gegevens eindigen voortijdig")
