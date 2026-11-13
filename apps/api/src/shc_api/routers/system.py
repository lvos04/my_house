from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text, update

from .. import backup as bk
from .. import events, notify, views
from ..adaptive import BASELINE_DAYS
from ..config import VERSION, get_settings
from ..deps import DB, Admin, Auth, WebAdmin
from ..errors import api_error, not_found, require_secret_box, secret_key_invalid
from ..logfilter import fouttekst as _fouttekst
from ..models import BackupRecord, Device, DeviceBinding, Home, Integration, LogicalRole
from ..security import lan_url_problem, normalize_server_url, remote_url_problem
from ..store import audit, get_setting, set_secret_setting, set_setting
from ..views.status import publish_status
from ..worker.aggregates import RETENTION_KEY, RETENTION_MAX_DAYS, RETENTION_MIN, load_retention, retention_defaults
from ..worker.backup_job import (
    auto_kind,
    backup_due,
    last_auto_times,
    load_backup_settings,
    next_due,
    retry_after,
    run_backup_process,
)
from ..worker.backup_job import latest as latest_time

router = APIRouter(tags=["system"])
log = logging.getLogger(__name__)

MANUAL_WAIT_S = 50.0  # a manual backup answers 200 when done within this time, else 202 (still running)
STALE_RUNNING = timedelta(hours=8)
BACKUP_BLOCKS_RESTORE = timedelta(hours=2)  # a "running" backup older than this is treated as crashed
UPLOAD_TTL = timedelta(hours=24)
MAINTENANCE_SETTLE_S = 12.0  # the worker checks the maintenance flag at most every 10 s
_background: set[asyncio.Task] = set()
_jobs: dict[str, dict] = {}


@router.get("/health")
async def health(db: DB) -> dict:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "version": VERSION}


@router.get("/system/status")
async def status(db: DB, p: Auth) -> dict:
    return {"checks": await views.system_checks(db), "version": VERSION}


# --- addresses (verbeterlijst 17: server side) ----------------------------------------------------


class Addresses(BaseModel):
    """Same rules as pairing (security.lan_url_problem / remote_url_problem), so the address saved here
    is always one a QR code can be made with."""

    lan_url: str | None = None
    remote_url: str | None = None

    @field_validator("lan_url")
    @classmethod
    def _lan(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        problem = lan_url_problem(v)
        if problem:
            raise ValueError(problem)
        return normalize_server_url(v)

    @field_validator("remote_url")
    @classmethod
    def _remote(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        problem = remote_url_problem(v)
        if problem:
            raise ValueError(problem)
        return normalize_server_url(v)


@router.get("/system/settings")
async def get_settings_(db: DB, p: Auth) -> dict:
    s = get_settings()
    return {
        "lan_url": await get_setting(db, "lan_url", s.lan_url),
        "remote_url": await get_setting(db, "remote_url", s.remote_url),
        "safe_mode": bool(await get_setting(db, "safe_mode", False)),
        "maintenance": bool(await get_setting(db, "maintenance", False)),
    }


@router.put("/system/settings")
async def put_settings(body: Addresses, db: DB, p: WebAdmin) -> dict:
    await set_setting(db, "lan_url", body.lan_url)
    await set_setting(db, "remote_url", body.remote_url)
    await audit(db, p.actor, "settings_updated", None, lan_url=body.lan_url, remote_url=body.remote_url)
    await db.commit()
    return await get_settings_(db, p)


@router.post("/system/safe-mode/release")
async def release_safe_mode(db: DB, p: Admin) -> dict:
    """User confirms device matching after restore/move; commands are allowed again."""
    await set_setting(db, "safe_mode", False)
    await audit(db, p.actor, "safe_mode_released")
    await db.flush()
    await publish_status(db)
    await db.commit()
    return {"safe_mode": False}


# --- backups ------------------------------------------------------------------------------------


def backup_view(b: BackupRecord) -> dict:
    v = bk.record_dict(b)
    if b.status == "running" and b.created_at and datetime.now(UTC) - b.created_at > STALE_RUNNING:
        v["status"] = "failed"
        v["error"] = "Back-up is niet afgerond (proces gestopt)."
    v["downloadable"] = v["status"] == "ok"
    return v


@router.get("/system/backups")
async def backups(db: DB, p: WebAdmin) -> list[dict]:
    rows = (await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(100))).scalars()
    return [backup_view(b) for b in rows]


class BackupIn(BaseModel):
    passphrase: str = Field(min_length=bk.MIN_PASSPHRASE, max_length=256)
    include_secrets: bool = True


async def _manual_backup(rec_id: uuid.UUID, passphrase: str, include_secrets: bool) -> dict:
    from ..db import sessionmaker

    args = ["--record-id", str(rec_id), "--passphrase-stdin"]
    if not include_secrets:
        args.append("--redact-secrets")
    try:
        result = await run_backup_process(args, stdin=(passphrase + "\n").encode())
    except Exception as exc:
        result = {"status": "failed", "error": f"Back-upproces kon niet starten: {_fouttekst(exc)}"}
    async with sessionmaker()() as db:
        if result.get("status") != "ok":
            rec = await db.get(BackupRecord, rec_id)
            if rec is not None and rec.status == "running":
                rec.status = "failed"
                rec.detail = {**(rec.detail or {}), "error": str(result.get("error"))[:500]}
                await db.flush()
        await publish_status(db)  # the overview's "Back-up" row (item 36)
        await db.commit()
    return result


def _spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)
    return t


@router.post("/system/backups")
async def create_backup(body: BackupIn, db: DB, p: WebAdmin):
    """Runs `shc-api backup` in a separate process (flat memory, API stays responsive). Answers 200
    with the finished backup, or 202 with status "running" when it takes longer; poll GET /system/backups."""
    rec = BackupRecord(kind="export", filename="(bezig)", status="running", auto=False, app_version=VERSION, detail={})
    db.add(rec)
    await audit(db, p.actor, "backup_started", None, include_secrets=body.include_secrets)
    await db.commit()
    rid = rec.id
    task = _spawn(_manual_backup(rid, body.passphrase, body.include_secrets))
    done, _ = await asyncio.wait({task}, timeout=MANUAL_WAIT_S)
    await db.refresh(rec)
    if rec.status == "failed":
        raise api_error(500, "backup_failed", (rec.detail or {}).get("error") or "Back-up mislukt.")
    if not done:
        return JSONResponse(status_code=202, content=json.loads(_dumps(backup_view(rec))))
    await audit(db, p.actor, "backup_created", rec.filename)
    await db.commit()
    return backup_view(rec)


def _dumps(o) -> str:
    from ..events import dumps

    return dumps(o)


@router.get("/system/backups/{backup_id}/download")
async def download(backup_id: uuid.UUID, db: DB, p: WebAdmin):
    b = await db.get(BackupRecord, backup_id)
    if not b or b.status != "ok":
        raise not_found("Back-up")
    bdir = get_settings().backup_dir.resolve()
    path = (bdir / b.filename).resolve()
    if not path.is_file() or path.parent != bdir:
        raise not_found("Back-upbestand")
    return FileResponse(path, filename=b.filename, media_type="application/octet-stream")


# --- backup settings (verbeterlijst 32) ---------------------------------------------------------


class BackupSettingsIn(BaseModel):
    """Omitted fields stay as they are. `passphrase` sets a new one; `clear_passphrase` removes it."""

    passphrase: str | None = None
    clear_passphrase: bool = False
    hour: int | None = None
    keep: int | None = None
    enabled: bool | None = None
    # "daily_full" (default) or "dump_daily_full_weekly": daily encrypted database dump, weekly full export.
    schedule: Literal["daily_full", "dump_daily_full_weekly"] | None = None

    @field_validator("passphrase")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if len(v) < bk.MIN_PASSPHRASE:
            raise ValueError(f"De wachtwoordzin moet minimaal {bk.MIN_PASSPHRASE} tekens zijn")
        if len(v) > 256:
            raise ValueError("De wachtwoordzin is te lang (maximaal 256 tekens)")
        return v

    @field_validator("hour")
    @classmethod
    def _hour(cls, v: int | None) -> int | None:
        if v is not None and not 0 <= v <= 23:
            raise ValueError("Het tijdstip moet een heel uur tussen 0 en 23 zijn")
        return v

    @field_validator("keep")
    @classmethod
    def _keep(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 365:
            raise ValueError("Bewaar tussen 1 en 365 automatische back-ups")
        return v


async def backup_settings_view(db) -> dict:
    cfg = await load_backup_settings(db)
    state = await get_setting(db, "backup_state", {}) or {}
    last_full, last_dump = await last_auto_times(db)
    last_auto = latest_time(last_full, last_dump)
    now = datetime.now(UTC)
    tz = ZoneInfo(cfg["timezone"])
    latest = (
        await db.execute(
            select(BackupRecord).where(BackupRecord.status == "ok").order_by(BackupRecord.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    latest_full = (
        await db.execute(
            select(BackupRecord)
            .where(BackupRecord.status == "ok", BackupRecord.kind != bk.DUMP_KIND)
            .order_by(BackupRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ra = retry_after(state)
    return {
        "enabled": cfg["enabled"],
        "wanted": cfg["wanted"],
        "passphrase_set": cfg["passphrase_set"],
        "passphrase_source": cfg["passphrase_source"],
        "passphrase_error": cfg["passphrase_error"],
        "hour": cfg["hour"],
        "keep": cfg["keep"],
        "timezone": cfg["timezone"],
        "schedule": cfg["schedule"],
        "next_kind": auto_kind(now, cfg["schedule"], last_full),
        "last_auto_backup": last_full,
        "last_auto_dump": last_dump,
        "next_due_at": next_due(now, cfg["hour"], tz, last_auto, state) if cfg["enabled"] else None,
        "due_now": cfg["enabled"] and backup_due(now, cfg["hour"], tz, last_auto, state),
        "last_error": state.get("last_error") if state.get("failures") else None,
        "failures": int(state.get("failures", 0) or 0),
        "retry_after": ra if ra and ra > now else None,
        "last_backup": backup_view(latest) if latest else None,
        "zigbee2mqtt_in_last_backup": ((latest_full.detail or {}).get("zigbee2mqtt_included") if latest_full else None),
    }


@router.get("/system/backup-settings")
async def get_backup_settings(db: DB, p: WebAdmin) -> dict:
    return await backup_settings_view(db)


@router.put("/system/backup-settings")
async def put_backup_settings(body: BackupSettingsIn, db: DB, p: WebAdmin) -> dict:
    if body.passphrase:
        require_secret_box()  # KLEIN-43: eerst de sleutel, dan pas iets opslaan
    stored = dict(await get_setting(db, "backup_settings", {}) or {})
    for k in ("hour", "keep", "enabled", "schedule"):
        v = getattr(body, k)
        if v is not None:
            stored[k] = v
    await set_setting(db, "backup_settings", stored)
    changed = {k: stored.get(k) for k in ("hour", "keep", "enabled", "schedule")}
    if body.passphrase:
        await set_secret_setting(db, "backup_passphrase_enc", body.passphrase)
        changed["passphrase"] = "gewijzigd"  # noqa: S105 - audit label, not a password
    elif body.clear_passphrase:
        await set_secret_setting(db, "backup_passphrase_enc", None)
        changed["passphrase"] = "verwijderd"  # noqa: S105 - audit label, not a password
    await audit(db, p.actor, "backup_settings_updated", None, **changed)
    await db.commit()
    return await backup_settings_view(db)


# --- push outside the app (verbeterlijst 28) ----------------------------------------------------


class NotifyIn(BaseModel):
    """``url``: ntfy topic URL (or another webhook) for "worker stopped" / "backups stopped"; null or
    empty removes the address set in the app (then SHC_NOTIFY_URL applies, if set)."""

    url: str | None = Field(default=None, max_length=500)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if problem := notify.url_problem(v):
            raise ValueError(problem)
        return v.strip()


async def _require_box_for_stored_notify(db) -> None:
    """KLEIN-43: staat er een push-adres in de app, dan is de sleutel nodig om het te lezen. Ontbreekt die of
    is hij te kort: 503 secret_key_invalid in plaats van 500 of de melding "andere hoofdsleutel"."""
    if await get_setting(db, notify.SETTING_KEY):
        require_secret_box()


async def notify_view(db) -> dict:
    await _require_box_for_stored_notify(db)
    url, source = await notify.configured_url(db)
    return {
        "configured": url is not None,
        "source": source,
        "url_hint": notify.url_hint(url),
        # The address set in the app does not decrypt with this server's master key (restore on a new
        # NAS, key change): it is not used; set it again (verbeterlijst 22).
        "error": await notify.stored_url_error(db),
        "events": [
            "De achtergronddienst reageert niet meer (en weer wel)",
            "Automatische back-ups mislukken of de laatste is te oud (en weer in orde)",
        ],
    }


@router.get("/system/notify")
async def get_notify(db: DB, p: WebAdmin) -> dict:
    return await notify_view(db)


@router.put("/system/notify")
async def put_notify(body: NotifyIn, db: DB, p: WebAdmin) -> dict:
    if body.url is not None:
        require_secret_box()
    await set_secret_setting(db, notify.SETTING_KEY, body.url)
    await audit(db, p.actor, "notify_url_updated", notify.url_hint(body.url), cleared=body.url is None)
    await db.commit()
    return await notify_view(db)


@router.post("/system/notify/test")
async def test_notify(db: DB, p: WebAdmin) -> dict:
    """Send a test message to the configured address; ``{ok, error}``."""
    await _require_box_for_stored_notify(db)
    url, _source = await notify.configured_url(db)
    if not url:
        raise api_error(409, "not_configured", "Stel eerst een push-adres in.")
    await db.commit()
    error = await notify.send(
        url,
        "Smart Home: testbericht",
        "Dit adres krijgt een bericht als de achtergronddienst of de back-ups stilvallen.",
        priority="default",
        tags="bell",
    )
    return {"ok": error is None, "error": error}


# --- retention (verbeterlijst 19) ---------------------------------------------------------------

RETENTION_LABELS = {
    "power_days": "Vermogen (detail)",
    "counter_days": "Meterstanden (ruw)",
    "log_days": "Logboek klimaatregeling",
    "sensor_days": "Sensormetingen (temperatuur, vochtigheid, batterij)",
}


class RetentionIn(BaseModel):
    """Days to keep raw data. Omitted fields stay as they are; null goes back to the default."""

    model_config = {"extra": "forbid"}

    power_days: int | None = None
    counter_days: int | None = None
    log_days: int | None = None
    sensor_days: int | None = None

    @field_validator("power_days", "counter_days", "log_days", "sensor_days")
    @classmethod
    def _range(cls, v: int | None, info) -> int | None:
        if v is None:
            return None
        low = RETENTION_MIN[info.field_name]
        if v < low:
            raise ValueError(f"{RETENTION_LABELS[info.field_name]}: bewaar minimaal {low} dagen")
        if v > RETENTION_MAX_DAYS:
            raise ValueError(f"{RETENTION_LABELS[info.field_name]}: maximaal {RETENTION_MAX_DAYS} dagen")
        return v


async def retention_view(db) -> dict:
    keep = await load_retention(db)
    return {
        "power_days": keep["power_days"],
        "counter_days": keep["counter_days"],
        "log_days": keep["log_days"],
        "sensor_days": keep["sensor_days"],
        "source": keep["source"],
        "defaults": retention_defaults(),
        "minimums": dict(RETENTION_MIN),
        "maximum": RETENTION_MAX_DAYS,
        "note": "Uurtotalen van energie en gas worden altijd bewaard. Meterstanden die nog niet in "
        "uurtotalen zijn verwerkt, worden nooit verwijderd. Sensormetingen blijven minstens "
        f"{RETENTION_MIN['sensor_days']} dagen bewaard: het adaptieve model gebruikt de laatste "
        f"{BASELINE_DAYS} dagen.",
    }


@router.get("/system/retention")
async def get_retention(db: DB, p: Admin) -> dict:
    return await retention_view(db)


@router.put("/system/retention")
async def put_retention(body: RetentionIn, db: DB, p: Admin) -> dict:
    stored = dict(await get_setting(db, RETENTION_KEY, {}) or {})
    for k in body.model_fields_set:
        v = getattr(body, k)
        if v is None:
            stored.pop(k, None)
        else:
            stored[k] = v
    await set_setting(db, RETENTION_KEY, stored)
    await audit(db, p.actor, "retention_updated", None, **{k: stored.get(k) for k in body.model_fields_set})
    await db.commit()
    return await retention_view(db)


# --- restore via upload (verbeterlijst 43, backend) ---------------------------------------------


def _uploads_dir() -> Path:
    d = get_settings().backup_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _upload_path(upload_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id or ""):
        raise not_found("Upload")
    return _uploads_dir() / f"{upload_id}.shcbak"


def _cleanup_uploads() -> None:
    cutoff = time.time() - UPLOAD_TTL.total_seconds()
    for p in _uploads_dir().glob("*"):
        with contextlib.suppress(OSError):
            if p.stat().st_mtime < cutoff:
                p.unlink()


class _Limit(Exception):
    pass


async def _stream_upload(request: Request, dest: Path, max_bytes: int) -> dict[str, str]:
    """Stream the request body to `dest` without buffering it. Accepts multipart/form-data with a
    `file` part (and optional small text fields such as `passphrase`), or a raw
    application/octet-stream body. Returns the text fields."""
    ctype = request.headers.get("content-type", "")
    fields: dict[str, str] = {}
    total = 0
    with open(dest, "wb") as out:  # noqa: ASYNC230 - small writes per received chunk
        if ctype.startswith("multipart/form-data"):
            m = re.search(r'boundary="?([^";]+)"?', ctype)
            if not m:
                raise api_error(422, "validation_error", "Ongeldige upload (boundary ontbreekt).")
            delim = b"--" + m.group(1).encode()
            body_delim = b"\r\n" + delim
            buf = b""
            state = "preamble"
            current: str | None = None
            field_buf = b""
            got_file = False
            async for chunk in request.stream():
                total += len(chunk)
                if total > max_bytes:
                    raise _Limit()
                buf += chunk
                while True:
                    if state == "preamble":
                        i = buf.find(delim)
                        if i < 0 or len(buf) < i + len(delim) + 2:
                            break
                        buf = buf[i + len(delim) :]
                        if buf.startswith(b"--"):
                            state = "end"
                            break
                        buf = buf[2:]  # \r\n
                        state = "headers"
                    elif state == "headers":
                        i = buf.find(b"\r\n\r\n")
                        if i < 0:
                            if len(buf) > 16384:
                                raise api_error(422, "validation_error", "Ongeldige upload (kop te groot).")
                            break
                        head = buf[:i].decode("utf-8", "replace")
                        buf = buf[i + 4 :]
                        nm = re.search(r'\bname="([^"]*)"', head)
                        is_file = "filename=" in head
                        if is_file:
                            fn = re.search(r'filename="([^"]*)"', head)
                            fields["filename"] = (fn.group(1) if fn else "")[:200]
                        current = "__file__" if is_file else (nm.group(1) if nm else "")
                        field_buf = b""
                        state = "body"
                    elif state == "body":
                        i = buf.find(body_delim)
                        if i < 0:
                            keep = len(body_delim) - 1
                            if len(buf) > keep:
                                data, buf = buf[:-keep], buf[-keep:]
                                if current == "__file__":
                                    out.write(data)
                                    got_file = True
                                else:
                                    field_buf += data
                                    if len(field_buf) > 4096:
                                        raise api_error(422, "validation_error", "Ongeldige upload (veld te groot).")
                            break
                        data = buf[:i]
                        if current == "__file__":
                            out.write(data)
                            got_file = True
                        else:
                            field_buf += data
                            if current:
                                fields[current] = field_buf.decode("utf-8", "replace")
                        buf = buf[i + len(body_delim) :]
                        if len(buf) < 2:
                            state = "after"
                            break
                        if buf.startswith(b"--"):
                            state = "end"
                            break
                        buf = buf[2:]
                        state = "headers"
                    elif state == "after":
                        if len(buf) < 2:
                            break
                        if buf.startswith(b"--"):
                            state = "end"
                            break
                        buf = buf[2:]
                        state = "headers"
                    else:
                        buf = b""
                        break
            if state != "end" or not got_file:
                raise api_error(422, "validation_error", "Upload onvolledig of zonder bestand (veld 'file').")
        else:
            async for chunk in request.stream():
                total += len(chunk)
                if total > max_bytes:
                    raise _Limit()
                out.write(chunk)
            if total == 0:
                raise api_error(422, "validation_error", "Leeg bestand ontvangen.")
    return fields


class _SecretKeyUnusable(Exception):
    """SHC_SECRET_KEY(_FILE) ontbreekt of SecretBox weigert hem (te kort). Draagt bewust geen tekst mee."""


def _decrypt_check(summary: dict) -> dict:
    """Which integration credentials and encrypted settings (backup passphrase, push address) in the
    backup can be decrypted with this server's key. Raises _SecretKeyUnusable when this server has no
    usable key (KLEIN-41)."""
    try:
        return bk.decrypt_check(summary.get("_integration_rows", []), settings=summary.get("_secret_settings", []))
    except (RuntimeError, ValueError):  # ontbrekende of te korte sleutel; nooit (een deel van) de sleutel tonen
        raise _SecretKeyUnusable() from None


def verify_summary(upload_id: str, path: Path, v: bk.Verified, filename: str | None) -> dict:
    m = v.manifest
    rel = bk.schema_relation(m.get("schema_revision"))
    refusal = None
    try:
        bk.check_restorable(m)
    except bk.BackupError as e:
        refusal = str(e)
    warnings = []
    if rel == "older":
        warnings.append(
            "Deze back-up komt van een oudere versie. Na het terugzetten wordt de database bijgewerkt; "
            "wat niet meer bestaat, wordt gemeld."
        )
    if m.get("redacted") or not m.get("secrets_included", True):
        warnings.append(
            "Geredigeerde back-up: wachtwoorden en apparaatsleutels ontbreken. Na het terugzetten is "
            "'shc-api reset-password' nodig en moet je apparaten opnieuw koppelen."
        )
    if not m.get("zigbee2mqtt_included"):
        warnings.append(
            "Geen (volledige) Zigbee2MQTT-gegevens: Zigbee-sensoren moeten mogelijk opnieuw gekoppeld worden."
        )
    creds = _decrypt_check(v.summary)
    if creds["failed"]:
        warnings.append(
            f"{len(creds['failed'])} apparaatsleutel(s) passen niet bij de hoofdsleutel van deze server. "
            "Zet de oude sleutel terug en gebruik 'shc-api rekey --old-key-file', of koppel die apparaten opnieuw."
        )
    if settings_warning := bk.key_check_warning(creds):
        warnings.append(settings_warning)
    return {
        "upload_id": upload_id,
        "filename": filename,
        "size_bytes": path.stat().st_size,
        "format_version": v.format_version,
        "created_at": m.get("created_at"),
        "app_version": m.get("app_version"),
        "schema_revision": m.get("schema_revision"),
        "current": {"app_version": VERSION, "schema_revision": bk.head_revision()},
        "schema_relation": rel,
        "restorable": refusal is None,
        "refusal": refusal,
        "secrets_included": bool(m.get("secrets_included")),
        "redacted": bool(m.get("redacted") or not m.get("secrets_included", True)),
        "zigbee2mqtt_included": bool(m.get("zigbee2mqtt_included")),
        "zigbee2mqtt": m.get("zigbee2mqtt"),
        "tables": {t: info["rows"] for t, info in m["tables"].items()},
        "total_records": sum(info["rows"] for info in m["tables"].values()),
        "homes": v.summary.get("homes", []),
        "devices": v.summary.get("devices", []),
        "integrations": v.summary.get("integrations", []),
        "credentials": creds,
        "warnings": warnings,
    }


def _verify(path: Path, passphrase: str) -> bk.Verified:
    v = bk.verify_file(path, passphrase, collect=True)
    return v


@router.post("/system/restore/verify")
async def restore_verify(request: Request, db: DB, p: WebAdmin) -> dict:
    """Upload a backup (multipart: `file` + `passphrase`, streamed to disk), decrypt and verify it.
    Nothing is changed. Returns versions, counts, homes and devices, and an `upload_id` for apply."""
    s = get_settings()
    _cleanup_uploads()
    length = int(request.headers.get("content-length") or 0)
    with contextlib.suppress(OSError):
        if length and shutil.disk_usage(s.backup_dir).free < length * 1.2:
            raise api_error(507, "disk_full", "Te weinig vrije schijfruimte voor deze upload.")
    upload_id = uuid.uuid4().hex
    path = _upload_path(upload_id)
    try:
        fields = await _stream_upload(request, path, s.max_upload_bytes)
    except _Limit as exc:
        path.unlink(missing_ok=True)
        raise api_error(
            413, "payload_too_large", f"Het bestand is te groot (maximaal {s.max_upload_bytes // 2**20} MB)."
        ) from exc
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    passphrase = fields.get("passphrase") or request.headers.get("x-backup-passphrase") or ""
    try:
        v = await asyncio.to_thread(_verify, path, passphrase)
    except bk.BackupError as e:
        path.unlink(missing_ok=True)
        raise api_error(422, "backup_invalid", str(e)) from e
    try:
        summary = verify_summary(upload_id, path, v, fields.get("filename"))
    except _SecretKeyUnusable:  # sleutel ontbreekt of is te kort (KLEIN-41): weigeren en upload opruimen
        path.unlink(missing_ok=True)
        raise secret_key_invalid() from None
    meta = {k: summary[k] for k in ("created_at", "app_version", "schema_revision", "schema_relation", "restorable")}
    path.with_suffix(".json").write_text(json.dumps(meta))
    await audit(db, p.actor, "restore_verified", upload_id, **meta)
    await db.commit()
    return summary


def _remove_upload(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_suffix(".json").unlink(missing_ok=True)


@router.delete("/system/restore/uploads/{upload_id}", status_code=204)
async def restore_discard(upload_id: str, p: WebAdmin) -> Response:
    _remove_upload(_upload_path(upload_id))
    return Response(status_code=204)


class ApplyIn(BaseModel):
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    passphrase: str = Field(min_length=1, max_length=256)
    mode: Literal["same_home", "new_home"] = "same_home"


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


async def _run_restore_job(job: dict, path: Path, passphrase: str, mode: str, maint_before: bool) -> None:
    from ..db import sessionmaker

    try:
        job["step"] = "wachten tot de worker pauzeert"
        await asyncio.sleep(MAINTENANCE_SETTLE_S)
        job["step"] = "controleren en terugzetten"

        def run() -> dict:
            return asyncio.run(bk.restore_file(path, passphrase, mode=mode)).as_dict()

        report = await asyncio.to_thread(run)
        job.update(state="done", step="klaar", report=report, finished_at=datetime.now(UTC))
        await asyncio.to_thread(_remove_upload, path)
        with contextlib.suppress(Exception):  # maintenance off, safe mode on: tell open screens
            async with sessionmaker()() as db:
                await publish_status(db)
                await db.commit()
    except Exception as exc:
        msg = str(exc) if isinstance(exc, bk.BackupError) else f"{exc.__class__.__name__}: {_fouttekst(exc)}"
        job.update(state="failed", step="mislukt", error=msg[:500], finished_at=datetime.now(UTC))
        log.warning("Herstel mislukt: %s", msg)
        with contextlib.suppress(Exception):
            async with sessionmaker()() as db:
                await set_setting(db, "maintenance", maint_before)
                await db.flush()
                await publish_status(db)
                await db.commit()


@router.post("/system/restore/apply", status_code=202)
async def restore_apply(body: ApplyIn, db: DB, p: WebAdmin) -> dict:
    """Start restoring a verified upload: maintenance mode on (the worker pauses), everything replaced
    in one transaction, safe mode on afterwards. Answers 202 with a job id; poll
    GET /system/restore/jobs/{job_id}. After success, log in again (sessions are not in backups)."""
    path = _upload_path(body.upload_id)
    if not path.is_file():
        raise not_found("Upload")
    if any(j["state"] == "running" for j in _jobs.values()):
        raise api_error(409, "restore_running", "Er loopt al een herstel.")
    busy = (
        await db.execute(
            select(BackupRecord.id).where(
                BackupRecord.status == "running", BackupRecord.created_at > datetime.now(UTC) - BACKUP_BLOCKS_RESTORE
            )
        )
    ).first()
    if busy:
        raise api_error(
            409, "backup_running", "Er loopt nog een back-up. Wacht tot die klaar is en probeer het dan opnieuw."
        )
    meta = {}
    with contextlib.suppress(OSError, ValueError):
        meta = json.loads(path.with_suffix(".json").read_text())
    if meta and not meta.get("restorable", True):
        raise api_error(409, "restore_refused", "Deze back-up kan niet hersteld worden (zie de controle).")

    def quick_check() -> None:  # wrong passphrase -> immediate answer (decrypts only the first chunk)
        _fmt, plain = bk.open_plaintext(path, body.passphrase)
        try:
            plain.read(1)
        finally:
            plain.close()

    try:
        await asyncio.to_thread(quick_check)
    except bk.BackupError as e:
        raise api_error(422, "backup_invalid", str(e)) from e
    maint_before = bool(await get_setting(db, "maintenance", False))
    await set_setting(db, "maintenance", True)
    await audit(db, p.actor, "restore_started", body.upload_id, mode=body.mode)
    await db.flush()
    await publish_status(db)
    await db.commit()
    job_id = secrets.token_urlsafe(24)
    job = {
        "job_id": job_id,
        "state": "running",
        "step": "gestart",
        "mode": body.mode,
        "started_at": datetime.now(UTC),
        "finished_at": None,
        "error": None,
        "report": None,
    }
    _jobs[job_id] = job
    _spawn(_run_restore_job(job, path, body.passphrase, body.mode, maint_before))
    return {"job_id": job_id, "status": "running", "poll": f"/api/v1/system/restore/jobs/{job_id}"}


@router.get("/system/restore/jobs/{job_id}")
async def restore_job(job_id: str) -> dict:
    """Progress of a restore. Needs no login (the database, including sessions, is being replaced);
    the job id is a long random secret only the admin who started the restore received."""
    job = _jobs.get(job_id)
    if not job:
        raise not_found("Herstelopdracht")
    return _job_view(job)


@router.get("/system/restore/compare")
async def restore_compare(db: DB, p: WebAdmin, home_id: uuid.UUID | None = None) -> dict:
    """Per device and per function: compatible / partial / unavailable, comparing the restored devices
    (snapshot taken at restore) with what the worker has seen since (identity, online, capabilities)."""
    snap = await get_setting(db, "restore_snapshot") or {}
    restored_at = datetime.fromisoformat(snap["restored_at"]) if snap.get("restored_at") else None
    before: dict = snap.get("devices") or {}
    homes = (
        (
            await db.execute(
                select(Home).where(Home.id == home_id) if home_id else select(Home).where(Home.active_to.is_(None))
            )
        )
        .scalars()
        .all()
    )
    home_ids = [h.id for h in homes]
    devices = (await db.execute(select(Device).where(Device.home_id.in_(home_ids)))).scalars().all()
    by_id = {d.id: d for d in devices}
    out_devices = []
    status_of: dict[uuid.UUID, tuple[str, str]] = {}
    for d in devices:
        b = before.get(str(d.id))
        seen = d.last_seen is not None and (restored_at is None or d.last_seen > restored_at)
        caps_now = set(d.capabilities or [])
        missing: list[str] = []
        if b is None and restored_at is not None:
            st, reason = "new", "Na het terugzetten gevonden; nog niet aan een functie gekoppeld."
        elif not (seen and d.online):
            st, reason = "unavailable", "Nog niet gezien sinds het terugzetten." if restored_at else "Offline."
        else:
            missing = sorted(set((b or {}).get("capabilities") or []) - caps_now)
            old_modes = set((((b or {}).get("caps") or {}).get("modes")) or [])
            new_modes = set((((d.state or {}).get("caps") or {}).get("modes")) or old_modes)
            missing += [f"modus {m}" for m in sorted(old_modes - new_modes)]
            if missing:
                st, reason = "partial", "Niet alles is beschikbaar: " + ", ".join(missing)
            else:
                st, reason = "compatible", "Gevonden op identiteit en bereikbaar."
        status_of[d.id] = (st, reason)
        out_devices.append(
            {
                "id": d.id,
                "home_id": d.home_id,
                "name": d.name,
                "provider": d.provider,
                "kind": d.kind,
                "stable_identity": d.stable_identity,
                "status": st,
                "reason": reason,
                "online": d.online,
                "last_seen": d.last_seen,
                "missing": missing,
            }
        )
    suggestion = {
        "unavailable": "Zet het apparaat aan of koppel het opnieuw; vervangen kan door een ander apparaat aan "
        "deze functie te koppelen (Apparaten).",
        "partial": "Controleer de functie; niet beschikbare acties blijven uitgeschakeld.",
        "compatible": None,
    }
    functions = []
    roles = (
        await db.execute(
            select(LogicalRole, DeviceBinding)
            .join(DeviceBinding, DeviceBinding.role_id == LogicalRole.id)
            .where(LogicalRole.home_id.in_(home_ids), DeviceBinding.effective_to.is_(None))
        )
    ).all()
    for role, bnd in roles:
        st, reason = status_of.get(bnd.device_id, ("unavailable", "Apparaat niet gevonden."))
        dev = by_id.get(bnd.device_id)
        functions.append(
            {
                "key": f"role:{role.key}",
                "label": role.label,
                "home_id": role.home_id,
                "device_id": bnd.device_id,
                "device_name": dev.name if dev else None,
                "status": "unavailable" if st == "new" else st,
                "reason": reason,
                "suggestion": suggestion.get(st),
            }
        )
    from ..models import ClimateZone

    for z in (await db.execute(select(ClimateZone).where(ClimateZone.home_id.in_(home_ids)))).scalars():
        for part, did in (("airco", z.unit_device_id), ("sensor", z.sensor_device_id)):
            if did is None:
                continue
            st, reason = status_of.get(did, ("unavailable", "Apparaat niet gevonden."))
            dev = by_id.get(did)
            functions.append(
                {
                    "key": f"zone:{z.id}:{part}",
                    "label": f"{z.name} ({'airco' if part == 'airco' else 'sensor'})",
                    "home_id": z.home_id,
                    "device_id": did,
                    "device_name": dev.name if dev else None,
                    "status": st,
                    "reason": reason,
                    "suggestion": suggestion.get(st),
                }
            )
    for d in devices:
        if d.kind == "light_group":
            st, reason = status_of[d.id]
            functions.append(
                {
                    "key": f"lights:{d.id}",
                    "label": f"Licht {d.name}",
                    "home_id": d.home_id,
                    "device_id": d.id,
                    "device_name": d.name,
                    "status": "compatible" if st == "new" else st,
                    "reason": reason,
                    "suggestion": suggestion.get(st),
                }
            )
    counts = {k: sum(1 for f in functions if f["status"] == k) for k in ("compatible", "partial", "unavailable")}
    return {
        "restored_at": restored_at,
        "safe_mode": bool(await get_setting(db, "safe_mode", False)),
        "restored_from": await get_setting(db, "restored_from"),
        "devices": out_devices,
        "functions": functions,
        "summary": counts,
    }


# --- homes --------------------------------------------------------------------------------------


@router.post("/homes/{home_id}/archive")
async def archive_home(home_id: uuid.UUID, db: DB, p: Admin) -> dict:
    """Moving out: the old home and its history stay, but become read-only/inactive. Its integrations
    are switched off, so the worker stops talking to the old devices."""
    h = await db.get(Home, home_id)
    if not h:
        raise not_found("Woning")
    h.active_to = h.active_to or datetime.now(UTC)
    res = await db.execute(
        update(Integration).where(Integration.home_id == home_id).values(enabled=False).returning(Integration.id)
    )
    integ_ids = list(res.scalars())
    await audit(db, p.actor, "home_archived", h.name, integrations_disabled=len(integ_ids))
    await db.flush()
    # The worker stops these adapters at once (its LISTEN wakes the reload loop) instead of within 2 min.
    for iid in integ_ids:
        await events.publish(db, "integration.changed", home_id, {"id": iid})
    await publish_status(db, [home_id])
    await db.commit()
    return views.home_view(h)
