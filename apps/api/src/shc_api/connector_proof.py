"""Controleer opdracht, richting, vorige stand en herstelstatus binnen dezelfde database."""

import asyncio
import hashlib
import json
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from .models import known_gree_control_fields

MEASUREMENT_PREFIX = "connector_measurement:"
MEASUREMENTS = f"LEFT JOIN settings m ON m.key = '{MEASUREMENT_PREFIX}' || c.id"
MEASURED_IDENTITY = "m.value->'v'->c.preview_hash"

def digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def apply_preview(action, diff, target, etag, recipe, *, legacy=False) -> str:
    content = {"action_id": action, "diff": diff, "blast_radius": [target],
               "resource_etag": etag, "rollback_recipe": recipe}
    if not legacy:
        content["direction"] = "apply"
    return digest(content)


# De oude migratie kon de tabel al hebben aangemaakt, met de geschiedenis nog in settings.
# Een sleutelconflict breekt de transactie af. Zo overschrijft geen van beide versies een bewijsrij.
RESTORE_HISTORY = """
    WITH history AS (
        DELETE FROM settings
        WHERE value->>'format' = 'connector-0007' AND key = 'connector_history:' || (value->'v'->>'id')
        RETURNING value->'v' AS proof
    )
    INSERT INTO connector_changes SELECT (jsonb_populate_record(NULL::connector_changes, proof)).* FROM history
    WHERE NOT EXISTS (SELECT 1 FROM connector_changes c WHERE c.id::text = proof->>'id' AND to_jsonb(c) = proof)
"""

# De zone kan intussen een andere unit hebben. De uitgevoerde opdracht bewaart de oorspronkelijke apparaat-ID.
REMEMBER_FIELDS = """
    WITH measured AS (
        SELECT DISTINCT cmd.device_id, measured_fields.field_name
        FROM connector_changes c JOIN commands cmd ON cmd.id = c.command_id AND cmd.source = 'connector'
        CROSS JOIN LATERAL (VALUES (c.before), (c.after), (c.rollback_from->'state'), (c.rollback_after)) s(state)
        CROSS JOIN LATERAL jsonb_object_keys(s.state->'controls') AS measured_fields(field_name)
        WHERE c.action_id = 'zone.command'
    ), fields AS (
        SELECT device_id, jsonb_agg(field_name ORDER BY field_name) AS known FROM measured GROUP BY device_id
    )
    UPDATE devices d SET state = jsonb_set(d.state, '{gree_control_fields}', (
        SELECT jsonb_agg(names.field_name ORDER BY names.field_name) FROM (
            SELECT DISTINCT raw.field_name FROM jsonb_array_elements_text(
                coalesce(d.state->'gree_control_fields', '[]'::jsonb) || fields.known
            ) AS raw(field_name)
        ) names
    ))
    FROM fields WHERE d.id = fields.device_id
      AND (d.state->'gree_control_fields' @> fields.known) IS NOT TRUE
"""

# Hetzelfde open herstel blokkeert een downgrade en wordt bij logisch herstel eerst veiliggesteld.
IN_FLIGHT = """
    EXISTS (SELECT 1 FROM commands cmd WHERE cmd.id IN (c.command_id, c.rollback_command_id)
            AND cmd.status IN ('sending', 'sent'))
"""
RESTORED = """
    c.rollback_status = 'confirmed' AND c.rollback_after = c.before AND c.rollback_at IS NOT NULL
    AND EXISTS (SELECT 1 FROM commands undo WHERE undo.id = c.rollback_command_id AND undo.status = 'confirmed')
"""
UNCHANGED = """
    c.status = 'confirmed' AND c.observed_at IS NOT NULL AND c.after_at IS NOT NULL AND c.after = c.before
    AND jsonb_typeof(c.before->'controls') = 'object' AND c.before->'controls' @> jsonb_build_object('TemUn', 0)
    AND c.parameters = jsonb_build_object('target_c', c.before->'target_c')
    AND c.rollback_command_id IS NULL AND c.rollback_status IS NULL
    AND EXISTS (SELECT 1 FROM commands a WHERE a.id = c.command_id AND a.status = 'confirmed')
"""
REFUSED = """
    c.status = 'failed' AND c.reason_code IN ('precondition_failed', 'readback_before_send_unavailable',
                                              'refused_before_send')
    AND c.after IS NULL AND c.after_at IS NULL AND c.rollback_command_id IS NULL AND c.rollback_status IS NULL
    AND EXISTS (SELECT 1 FROM commands a WHERE a.id = c.command_id AND a.status IN ('failed', 'denied', 'cancelled'))
"""
PROPOSAL_SCOPE = "(k.scope = 'connector:voorstel' OR k.scope LIKE 'connector:voorstel:%')"
LOCAL_RIGHT = f"EXISTS (SELECT 1 FROM idempotency_keys k WHERE k.key = c.id AND {PROPOSAL_SCOPE})"  # noqa: S608 - constante scope
# Oud schemaherstel liet de oorspronkelijke bewijsrij op queued staan. Alleen de
# wachtrij, de claimteller en het ingetrokken recht bewijzen de annulering.
CANCELLED_APPLY = f"""
    cmd.status = 'cancelled' AND cmd.attempts = 0
    AND (c.status = 'queued' AND c.reason_code IS NULL OR c.status = 'failed'
         AND c.reason_code IN ('refused_before_send','precondition_failed','readback_before_send_unavailable',
                               'restored_without_execution','versiewissel'))
    AND c.after IS NULL AND c.after_at IS NULL
    AND c.rollback_command_id IS NULL AND c.rollback_status IS NULL AND c.rollback_at IS NULL
    AND c.rollback_after IS NULL AND c.rollback_reason_code IS NULL AND c.rollback_expires_at IS NULL
    AND (c.rollback_from IS NULL OR c.rollback_from - 'measurement_hash' = jsonb_build_object())
    AND NOT {LOCAL_RIGHT}
"""
CANCELLED_UNDO = f"""
    EXISTS (SELECT 1 FROM commands u WHERE u.id = c.rollback_command_id AND u.source = 'connector'
            AND u.status = 'cancelled' AND u.attempts = 0)
    AND c.rollback_status = 'queued' AND c.rollback_at IS NULL AND c.rollback_after IS NULL
    AND c.rollback_reason_code IS NULL AND NOT {LOCAL_RIGHT}
"""  # noqa: S608 - vaste bewijsvoorwaarde
CANCELLED = f"(({CANCELLED_APPLY}) OR ({CANCELLED_UNDO}))"
# Een geclaimde, onderbroken verzending houdt haar eigen bewijs van onzekere uitkomst.
# Een wachtrijannulering met een claim is daarmee in strijd, ook als de detailtekst is veranderd.
CANCELLATION_RECORD = """cmd.status = 'cancelled' AND (cmd.attempts = 0 OR
    cmd.detail IN ('Connectorbediening vervallen bij schemaherstel', 'versiewissel') OR {phase} = 'queued')"""
INVALID_CANCELLATION = f"""
    (({CANCELLATION_RECORD.format(phase="c.status")}) AND ({CANCELLED_APPLY}) IS NOT TRUE)
    OR (EXISTS (SELECT FROM commands cmd WHERE cmd.id=c.rollback_command_id
                AND ({CANCELLATION_RECORD.format(phase="c.rollback_status")}))
        AND ({CANCELLED_UNDO}) IS NOT TRUE)
"""  # noqa: S608 - vaste bewijsvoorwaarden
OLD_STATE = """
    c.before = jsonb_build_object('power', c.before->'power', 'mode', c.before->'mode',
                                  'target_c', c.before->'target_c')
"""
OLD_PAIR = f"({RESTORED}) AND c.status = 'confirmed' AND c.rollback_from = c.after AND ({OLD_STATE})"
OPEN_RECOVERY = f"""
    c.command_id IS NOT NULL AND (
        {IN_FLIGHT} OR (
            (({RESTORED}) AND jsonb_typeof(c.before->'controls') = 'object' OR ({OLD_PAIR})) IS NOT TRUE
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
# afgehandeld paar of een bewezen weigering vóór verzending mag met die historische metingen mee.
INCOMPLETE_PROOF = f"""
    WITH proof AS (
        SELECT c.*, (
            ({OLD_PAIR}) OR (({OLD_STATE}) AND (({REFUSED}) OR ({CANCELLED_APPLY})
               OR (({CANCELLED_UNDO}) AND c.rollback_from = c.after)))
        ) IS TRUE AS historical
        FROM connector_changes c LEFT JOIN commands cmd ON cmd.id = c.command_id
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
          AND (l.id IS NULL OR (
              l.action_id = 'zone.command' AND cmd.action = 'set_hvac'
              AND l.target->>'zone_id' = cmd.zone_id::text AND l.observed_at IS NOT NULL
              AND EXISTS (SELECT 1 FROM devices d WHERE d.id = cmd.device_id AND d.home_id = cmd.home_id)
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
        UNION ALL
        SELECT 1 FROM connector_changes c LEFT JOIN commands cmd ON cmd.id=c.command_id
        WHERE {INVALID_CANCELLATION}
    )
"""  # noqa: S608 - RESTORED is vaste broncode


# Lees de getallen in dezelfde JSON-vorm als bij het voorstel (20 en 20.0 hebben een andere hash).
# Alleen een volledige, nog open opdracht moet ook alle nu bekende onafhankelijke velden kunnen herstellen.
PREVIEWS = f"""
    SELECT jsonb_build_object(
        'action', c.action_id, 'target', c.target, 'parameters', c.parameters, 'etag', c.resource_etag,
        'before', c.before, 'hash', c.preview_hash, 'linked', c.command_id IS NOT NULL,
        'settled', ({RESTORED}) OR ({UNCHANGED}) OR ({REFUSED}) OR ({CANCELLED}),
        'measurement', {MEASURED_IDENTITY}, 'device_id', cmd.device_id,
        'invalid_identity', (m.key IS NOT NULL AND c.before ? 'controls'
                             AND jsonb_typeof({MEASURED_IDENTITY}) IS DISTINCT FROM 'object')
                            OR (c.rollback_from ? 'measurement_hash'
                                AND c.rollback_from->>'measurement_hash'
                                    IS DISTINCT FROM ({MEASURED_IDENTITY})->>'hash'),
        'state', d.state, 'capabilities', d.capabilities, 'half', i.config->'half_degrees'
    )::text
    FROM connector_changes c LEFT JOIN commands cmd ON cmd.id = c.command_id
    {MEASUREMENTS}
    LEFT JOIN devices d ON d.id = cmd.device_id
    LEFT JOIN integrations i ON i.id = d.integration_id
    WHERE c.action_id = 'zone.command'
      AND (c.before IS NOT NULL OR c.observed_at IS NOT NULL OR c.command_id IS NOT NULL OR c.status = 'proposed')
"""  # noqa: S608 - vaste bewijsvoorwaarden


def check_preview(raw: str) -> None:
    from .backup import BackupError

    saved = json.loads(raw)
    before = saved["before"]
    invalid = BackupError("Terugzetten geweigerd: het connectorherstelbewijs wijkt af van de oorspronkelijke preview.")
    if (not isinstance(before, dict) or "target_c" not in before or saved["invalid_identity"]):
        raise invalid
    diff = {"before": before, "proposed": saved["parameters"]}
    recipes = [{"target_c": before["target_c"]}]
    if not saved["linked"]:
        recipes.append(None)  # Een incompatibele meting levert een voorstel zonder herstelrecept op.
    hashes = (apply_preview(saved["action"], diff, saved["target"], saved["etag"], recipe,
                            legacy="controls" not in before) for recipe in recipes)
    if saved["hash"] not in hashes:
        raise invalid
    measurement = saved["measurement"]
    if measurement is not None:
        if not isinstance(measurement, dict) or set(measurement) != {"unit_id", "zone_id", "outdoor_group", "binding",
                                                                     "hash"}:
            raise invalid
        seal = measurement.pop("hash")
        if (measurement["zone_id"] != saved["target"].get("zone_id")
            or saved["linked"] and measurement["unit_id"] != saved["device_id"]
            or digest({"binding": measurement["binding"], "before": before}) != saved["etag"]
            or digest({"identity": measurement, "resource_etag": saved["etag"]}) != seal):
            raise invalid
    if saved["linked"] and not saved["settled"]:
        required = known_gree_control_fields(saved["state"] or {}, saved["capabilities"] or [])
        required.difference_update({"Pow", "Mod", "SetTem", "TemSen"})
        if saved["half"]:
            required.discard("TemRec")  # In halvegraadmodus is deze bit opgenomen in target_c.
        controls = before.get("controls")
        if not isinstance(controls, dict) or not required.issubset(controls):
            raise invalid



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
    tables = {name: Base.metadata.tables[name] for name in (
        "commands", "connector_changes", "settings", "devices", "integrations", "idempotency_keys",
    )}
    async with _proof_database(url) as db:
        for name, model in tables.items():
            # Geen functies, triggers of rechten uit het archief. De tabellen bevatten alleen onze kolommen en typen.
            table = Table(name, MetaData(), *(Column(col.name, col.type, primary_key=col.primary_key)
                                              for col in model.columns))
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
        await db.execute(RESTORE_HISTORY)
        await db.execute(REMEMBER_FIELDS)
        if await db.fetchval(INCOMPLETE_PROOF):
            raise BackupError("Terugzetten geweigerd: het connectorherstelbewijs ontbreekt of is onvolledig.")
        async with db.transaction():
            async for row in db.cursor(PREVIEWS):
                check_preview(row[0])


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
        # Configuratie en status zijn nodig voor de bedienvelden. Apparaatsleutels horen niet in de proefdatabase.
        projection = {
            "devices": ("id", "home_id", "state", "capabilities", "integration_id"), "integrations": ("id", "config"),
            "idempotency_keys": ("scope", "key"),
        }
        columns = projection.get(name, fields)
        await db.copy_to_table(name, columns=columns, source=_copy_rows(stream, name, fields, columns))
    if "commands" not in seen:
        raise ValueError("De opdrachten ontbreken in de database-dump")


async def _copy_rows(stream, table_name, fields, columns):
    # PostgreSQL COPY-tekst ontsnapt tabs en regeleinden in veldwaarden. Laat PostgreSQL de waarden decoderen.
    selector = {"settings": "key", "commands": "source"}.get(table_name)
    index = fields.index(selector) if selector else None
    indexes = [fields.index(col) for col in columns] if columns != fields else None
    while line := await stream.readline():
        if line == b"\\.\n":
            return
        if index is not None:
            field = line.rstrip(b"\n").split(b"\t")[index]
            if table_name == "commands" and field != b"connector":
                continue
            if table_name == "settings" and not field.startswith((b"connector_history:", MEASUREMENT_PREFIX.encode())):
                continue
        if indexes:
            parts = line.rstrip(b"\n").split(b"\t")
            line = b"\t".join(parts[i] for i in indexes) + b"\n"
        yield line
    raise ValueError("De COPY-gegevens eindigen voortijdig")
