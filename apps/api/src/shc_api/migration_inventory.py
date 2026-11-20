"""Inhoudsvergelijking van de overdraagbare rijen, vanaf het bronsnapshot tot de vrijgave."""
from __future__ import annotations

import base64
import hashlib
import json

from sqlalchemy import func, select, text

from . import backup as bk
from .config import get_settings
from .models import Base
from .security import SecretBox

# Het model bepaalt de tabellen en kolommen. Alleen waarnemingen van de draaiende dienst
# vallen buiten de configuratievergelijking. Bereikbaarheid en capabilities hebben ook een liveproef.
RUNTIME_COLUMNS = {
    "users": {"failed_logins", "locked_until"},
    "api_clients": {"last_used_at"},
    "devices": {"online", "last_seen", "diagnostics", "connector_version"},
    "integrations": {"state", "detail", "last_ok_at", "diagnostics"},
    "climate_zones": {"last_command_at", "last_commanded", "start_times", "pending_command_id", "pending_since",
                      "mismatch_count", "connector_version", "last_decision", "watchdog_since"},
}
LOCAL_SETTINGS = {"commissioning_gate", "server_id", "safe_mode", "maintenance", "restored_from", "restore_report",
                  "restore_snapshot",
                  "migration_pending", "migration_source_pause", "status_watch", "worker_heartbeat", "worker_loops",
                  "clock_check", "backup_state"}
# Scènes, groepsleden, brugregels, knoppen, gebeurtenissen en adaptermogelijkheden blijven in de vergelijking.
LIVE_DEVICE_STATE = {"last_event", "on", "brightness", "connectivity", "power", "mode", "target_c", "fan", "swing",
                     "unit_temperature", "temperature", "humidity", "battery", "linkquality", "relay_on", "power_w"}
APPEND_ONLY = {"sensor_readings", "audit_log", "zone_log"}


def tables():
    return [table for table in Base.metadata.sorted_tables if table.name not in bk.SKIP_TABLES]


def lock_tables(conn):
    names = ", ".join(conn.dialect.identifier_preparer.quote(t.name) for t in tables())
    conn.execute(text(f"LOCK TABLE {names} IN SHARE MODE"))


def fingerprint(conn, table, *, boundary=None):
    name = table.name
    cols = [c for c in table.columns if c.name not in RUNTIME_COLUMNS.get(name, ())]
    stmt = select(*cols).order_by(*table.primary_key)
    if name in APPEND_ONLY:
        if boundary is None:
            boundary = conn.scalar(select(func.max(table.c.id))) or 0
        stmt = stmt.where(table.c.id <= boundary)
    if name == "settings":
        stmt = stmt.where(table.c.key.not_in(LOCAL_SETTINGS))
    # Een versleuteling kan na rekey andere bytes hebben terwijl de inhoud gelijk is.
    box = None
    digest = hashlib.sha256()
    count = 0
    readable = True
    for row in conn.execute(stmt.execution_options(yield_per=1000)).mappings():
        value = dict(row)
        if name == "devices":
            value["state"] = {k: v for k, v in (value["state"] or {}).items() if k not in LIVE_DEVICE_STATE}
        encrypted = None
        if name == "integrations":
            encrypted = value.get("credentials_enc")
        elif name == "settings" and value["key"] in bk.SECRET_SETTING_KEYS:
            encrypted = value["value"].get("v")
        if encrypted:
            try:
                if name == "settings":
                    encrypted = base64.b64decode(encrypted, validate=True)
                box = box or SecretBox(get_settings().resolved_secret_key())
                value["credentials_enc" if name == "integrations" else "value"] = box.decrypt(bytes(encrypted))
            except Exception:
                readable = False
        encoded = json.dumps(value, default=bk._json_default, sort_keys=True, separators=(",", ":")).encode()
        digest.update(encoded + b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest(), "boundary": boundary, "readable": readable}


def capture(conn):
    return {table.name: fingerprint(conn, table) for table in tables()}


def comparison(conn, expected):
    result = {}
    expected = expected if isinstance(expected, dict) else {}
    for table in tables():
        before = expected.get(table.name) or {}
        after = fingerprint(conn, table, boundary=before.get("boundary"))
        result[table.name] = {"source": before.get("rows"), "target": after["rows"],
                              "same": after["readable"] and before == after}
    return result
