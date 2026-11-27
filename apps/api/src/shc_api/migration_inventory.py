"""Inhoudsvergelijking van de brondatabase tot de vrijgave op het doel."""
from __future__ import annotations

import base64
import hashlib

from sqlalchemy import MetaData, func, inspect, select

from . import backup as bk
from .config import get_settings
from .models import Base
from .security import SecretBox

# Alle overige kolommen, ook nieuwe, gaan integraal de hash in.
RUNTIME_COLUMNS = {
    "users": {
        "failed_logins": "Een nieuwe doelpoging wijzigt de actuele vergrendelingsteller.",
        "locked_until": "Een nieuwe doelpoging kan de lokale vergrendeling doen ingaan of vervallen.",
    },
    "api_clients": {"last_used_at": "Een leesverzoek op de gekoppelde telefoon ververst dit tijdstip."},
    "devices": {
        "online": "Het doel moet de bereikbaarheid na herstel opnieuw meten.",
        "last_seen": "De liveproef vereist een waarneming van na het herstel.",
        "connector_version": "De lokale databasetrigger telt ook verse waarnemingen op het doel.",
    },
    "integrations": {
        "state": "De adapter rapporteert zijn huidige verbinding met het doel.",
        "detail": "De adapter ververst de toelichting bij de huidige verbinding.",
        "last_ok_at": "Een geslaagde lokale verbindingscontrole ververst dit tijdstip.",
    },
    "climate_zones": {"connector_version": "De lokale trigger telt wijzigingen van de regelaar."},
}
LOCAL_SETTINGS = {
    "commissioning_gate": "De eindvergelijking toetst het volledige, samengevoegde inbedrijfstellingsbeleid apart.",
    "server_id": "Het bronbewijs en de bevestiging toetsen de afzonderlijke bron- en doelidentiteit.",
    "safe_mode": "Het herstel schakelt veilige modus in tot vrijgave.",
    "maintenance": "De schrijfpauze en de hersteltaak beheren deze bedrijfsstand.",
    "restored_from": "Herkomst van de laatst op deze installatie teruggezette kopie.",
    "restore_report": "Het nieuwe herstel vervangt het lokale taakrapport.",
    "restore_snapshot": "De actuele herstelproef vervangt de lokale apparaatvergelijking.",
    "migration_pending": "Dit is de lokale taak inclusief de inventaris die hier wordt vergeleken.",
    "migration_source_pause": "De schrijfgrens hoort uitsluitend op de bron.",
}
# Velden van installatielokale herstelbewijzen. De afgeleide inventaris en momentopname bestaan
# uit gegevens die zelf al als brontabellen worden vergeleken. Nieuwe nevengeschikte velden tellen mee.
LOCAL_PROOF_FIELDS = {
    "commissioning_gate": {"version", "enabled", "origin", "enabled_at", "enabled_by", "restored"},
    "restored_from": {"created_at", "app_version", "schema_revision", "restored_at", "mode"},
    "restore_report": {"schema_from", "schema_to", "migrated", "counts", "total_records", "dropped", "mode",
                       "new_home_id", "archived_homes", "zigbee2mqtt_included", "interrupted_backups", "drafts"},
    "restore_snapshot": {"restored_at", "devices", "homes", "mode", "active_homes", "functions",
                         "definitions", "buttons"},
    "migration_pending": {"source_id", "target_id", "source_pause", "inventory", "gate", "archive_sha256",
                          "rollback_sha256", "comparison_id"},
    "migration_source_pause": {"id", "source_id", "started", "stopped_at", "expires", "maintenance_before",
                               "storage", "last_reading", "reading_count", "publishing", "file_id", "interim_file_id"},
}
RUNTIME_SETTINGS = {
    "worker_heartbeat": {("v",): "Het draaiende proces ververst zijn levensteken."},
    "status_watch": {("v", key): "Laatste waarneming van de lokale bewaker."
                     for key in ("worker", "backup", "worker_down", "backup_problem")},
    "clock_check": {("v", key): "Uitkomst en tijdstip van de lokale klokmeting."
                    for key in ("state", "detail", "offset_s", "delay_s", "server", "checked_at")},
    "worker_loops": {("v", key): "Momentopname van het nieuwe workerproces."
                     for key in ("at", "started_at", "integrations")},
}
# Alleen benoemde scalaire bladeren mogen verversen. Ook binnen deze objecten telt ieder nieuw veld mee.
RUNTIME_PATHS = {
    "devices": {
        ("diagnostics", "inventory", "seen_at"): "Tijdstip van de laatste inventarisronde.",
        **{("state", "last_event", key): "Het laatste live knopbericht van de adapter."
           for key in ("button", "event", "at")},
        **{("state", key): "De adapter ververst deze gemeten apparaatstand."
           for key in ("on", "brightness", "connectivity", "power", "mode", "target_c", "fan", "swing",
                       "unit_temperature", "temperature", "humidity", "battery", "linkquality", "relay_on", "power_w")},
    },
    "integrations": {
        **{("diagnostics", "health", key): "Uitkomst van de laatste gezondheidspeiling."
           for key in ("state", "code", "detail", "checked_at")},
        **{("diagnostics", "status", key): "Momentopname van de huidige verbinding."
           for key in ("state", "code", "detail", "since", "missed_polls", "consecutive_poll_failures")},
        **{("diagnostics", "polls", key): "Teller van de lopende peilronde."
           for key in ("missed", "consecutive_failures")},
    },
}
APPEND_ONLY = {"sensor_readings", "audit_log", "zone_log"}

# Deze procesjournalen zijn geen tweede kopie op het doel. Hun gegevens krijgen elk een bestemming.
LOCAL_JOURNALS = {
    ("shc_pause", "attempts"): "De export verwerkt de actuele vergrendelingen transactioneel in users.",
    ("shc_pause", "events"): "De export verplaatst alle gebeurtenissen transactioneel naar audit_log.",
    ("shc_restore", "jobs"): "Lokale taakcodes geven toegang tot de terugvalbestanden op deze installatie.",
    ("shc_restore", "unconfirmed_exports"): "Onbevestigde bestanden horen bij de archiefmap van deze installatie.",
}


def table_key(table):
    return table.name if table.schema in (None, "public") else table.fullname


def catalog(conn):
    metadata = MetaData()
    for schema in inspect(conn).get_schema_names():
        if schema != "information_schema" and not schema.startswith("pg_"):
            metadata.reflect(bind=conn, schema=schema, resolve_fks=False, postgresql_ignore_search_path=True)
    return {table_key(t): t for t in metadata.sorted_tables if (t.schema, t.name) not in LOCAL_JOURNALS}


def tables(conn=None):
    all_tables = Base.metadata.sorted_tables if conn is None else catalog(conn).values()
    # De Alembic-revisie staat afzonderlijk in het manifest en wordt bij herstel getoetst.
    return [t for t in all_tables if table_key(t) != "alembic_version" and table_key(t) not in bk.SKIP_TABLES]


def lock_tables(conn):
    names = ", ".join(conn.dialect.identifier_preparer.format_table(t) for t in tables(conn))
    conn.exec_driver_sql(f"LOCK TABLE {names} IN SHARE MODE")


def _omit_live_leaf(obj, path):
    key, *rest = path
    if not isinstance(obj, dict) or key not in obj:
        return False
    child = obj[key]
    if rest:
        removed = _omit_live_leaf(child, rest)
        if removed and child == {}:
            del obj[key]
        return removed
    if not isinstance(child, (list, dict)):
        del obj[key]
        return True
    return False


def _repeated_rows(conn, table, boundary):
    # Eigen nieuwe doelhistorie mag naast de bronhistorie staan. Een gekopieerde brongebeurtenis
    # met een nieuw volgnummer telt wel als extra rij, ook wanneer het oorspronkelijke ID nog bestaat.
    extra = table.alias()
    # Dezelfde PostgreSQL-notatie voor beide rijen onderscheidt ook SQL NULL, JSON null,
    # getalschaal en de oorspronkelijke tekst van een json-kolom zonder gelijkheidsoperator.
    same = [bk.archive_text(col).collate("C").is_not_distinct_from(bk.archive_text(extra.c[col.name]).collate("C"))
            for col in table.columns if col.name != "id"]
    original = select(1).select_from(table).where(table.c.id <= boundary, *same).exists()
    return conn.scalar(select(func.count()).select_from(extra).where(extra.c.id > boundary, original))


def _setting_has_data(row):
    key, payload = row["key"], row["value"]
    if not isinstance(payload, dict):
        return True
    if key in LOCAL_SETTINGS:
        value = payload.get("v")
        if isinstance(value, dict):
            for field in LOCAL_PROOF_FIELDS.get(key, ()):
                value.pop(field, None)
            if not value:
                payload.pop("v")
        else:
            _omit_live_leaf(payload, ("v",))
    elif key in RUNTIME_SETTINGS:
        if key == "worker_loops" and isinstance(payload.get("v"), dict):
            loops = payload["v"].get("loops")
            if isinstance(loops, dict):
                for loop in list(loops):
                    for field in ("last", "interval", "stuck"):  # Levensteken en actuele bewaking per lus.
                        _omit_live_leaf(payload, ("v", "loops", loop, field))
        for path in RUNTIME_SETTINGS[key]:
            _omit_live_leaf(payload, path)
    else:
        return True
    return bool(payload) or any(row[c] is not None for c in row.keys() - {"key", "value"})


def compared_columns(table):
    return [c for c in table.columns if c.name not in RUNTIME_COLUMNS.get(table_key(table), ())]


def fingerprint(conn, table, *, boundary=None):
    name = table_key(table)
    cols = compared_columns(table)
    conn.execute(bk.ARCHIVE_TEXT_SETTINGS)
    stmt = select(*bk.archive_columns(cols)).order_by(*bk.archive_order(table, cols))
    count = 0
    if name in APPEND_ONLY:
        if boundary is None:
            boundary = conn.scalar(select(func.max(table.c.id))) or 0
        else:
            count = _repeated_rows(conn, table, boundary)
        stmt = stmt.where(table.c.id <= boundary)
    box = None
    digest = hashlib.sha256()
    readable = True
    for row in conn.execute(stmt.execution_options(yield_per=1000)).mappings():
        value = dict(row)
        if name == "settings" and not _setting_has_data(value):
            continue
        for path in RUNTIME_PATHS.get(name, ()):
            _omit_live_leaf(value, path)
        encrypted = None
        if name == "integrations":
            encrypted = value.get("credentials_enc")
        elif name == "settings" and value["key"] in bk.SECRET_SETTING_KEYS:
            encrypted = value["value"].get("v")
        if encrypted:
            try:
                box = box or SecretBox(get_settings().resolved_secret_key())
                if name == "settings":
                    plain = box.decrypt(base64.b64decode(encrypted, validate=True))
                    value["value"] = {**value["value"], "v": plain}
                else:
                    value["credentials_enc"] = box.decrypt(bytes(encrypted))
            except Exception:
                readable = False
        digest.update(bk.comparison_bytes(value) + b"\n")
        count += 1
    return {"columns": sorted(c.name for c in cols), "types": bk.archive_types(cols, conn.dialect),
            "rows": count, "sha256": digest.hexdigest(),
            "boundary": boundary, "readable": readable}


def capture(conn, source_tables=None):
    return {table_key(t): fingerprint(conn, t) for t in (tables(conn) if source_tables is None else source_tables)}


def comparison(conn, expected):
    expected = expected if isinstance(expected, dict) else {}
    actual = {table_key(t): t for t in tables(conn)}
    result = {}
    for name in sorted(actual.keys() | expected.keys()):
        before = expected.get(name) or {}
        after = {}
        if name in actual:
            after = fingerprint(conn, actual[name], boundary=before.get("boundary"))
        result[name] = {"source": before.get("rows"), "target": after.get("rows"),
                        "same": bool(after.get("readable")) and before == after}
    return result
