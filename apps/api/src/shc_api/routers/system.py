from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select, text

from .. import backup as bk
from .. import views
from ..config import VERSION, get_settings
from ..deps import DB, Admin, Auth
from ..errors import api_error, not_found
from ..models import BackupRecord, Home
from ..store import audit, get_setting, set_setting

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(db: DB) -> dict:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "version": VERSION}


@router.get("/system/status")
async def status(db: DB, p: Auth) -> dict:
    s = get_settings()
    checks = []
    rev = await bk.schema_revision(db)
    checks.append(
        {
            "key": "database",
            "label": "Database",
            "state": "ok" if rev else "warn",
            "detail": f"Schema {rev}" if rev else "Migraties niet gevonden",
        }
    )
    hb = await get_setting(db, "worker_heartbeat")
    if hb:
        age = (datetime.now(UTC) - datetime.fromisoformat(hb)).total_seconds()
        checks.append(
            {
                "key": "worker",
                "label": "Achtergronddienst",
                "state": "ok" if age < 120 else "error",
                "detail": f"Laatste hartslag {int(age)} s geleden",
            }
        )
    else:
        checks.append(
            {"key": "worker", "label": "Achtergronddienst", "state": "error", "detail": "Geen hartslag"}
        )
    try:
        du = shutil.disk_usage(s.backup_dir if s.backup_dir.exists() else "/")
        free = du.free / du.total
        checks.append(
            {
                "key": "disk",
                "label": "Vrije schijfruimte",
                "state": "ok" if free > 0.15 else "warn",
                "detail": f"{du.free // 2**30} GB vrij ({free:.0%})",
            }
        )
    except OSError:
        pass
    last = (
        await db.execute(
            select(BackupRecord)
            .where(BackupRecord.status == "ok")
            .order_by(BackupRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last:
        age_h = (datetime.now(UTC) - last.created_at).total_seconds() / 3600
        checks.append(
            {
                "key": "backup",
                "label": "Back-up",
                "state": "ok" if age_h < 36 else "warn",
                "detail": f"Laatste back-up {last.created_at:%d-%m %H:%M} UTC",
            }
        )
    else:
        checks.append(
            {"key": "backup", "label": "Back-up", "state": "warn", "detail": "Nog geen back-up gemaakt"}
        )
    ntp = await get_setting(db, "clock_check")
    if ntp:
        checks.append(
            {
                "key": "time",
                "label": "Tijdsynchronisatie",
                "state": ntp.get("state", "unknown"),
                "detail": ntp.get("detail", ""),
            }
        )
    if await get_setting(db, "safe_mode", False):
        checks.append(
            {
                "key": "safe_mode",
                "label": "Veilige modus",
                "state": "warn",
                "detail": "Geen apparaatcommando's tot je de koppelingen bevestigt.",
            }
        )
    checks += await views.status_checks(db)
    return {"checks": checks, "version": VERSION}


class Addresses(BaseModel):
    lan_url: HttpUrl | None = None
    remote_url: HttpUrl | None = None


@router.get("/system/settings")
async def get_settings_(db: DB, p: Auth) -> dict:
    s = get_settings()
    return {
        "lan_url": await get_setting(db, "lan_url", s.lan_url),
        "remote_url": await get_setting(db, "remote_url", s.remote_url),
        "safe_mode": bool(await get_setting(db, "safe_mode", False)),
    }


@router.put("/system/settings")
async def put_settings(body: Addresses, db: DB, p: Admin) -> dict:
    await set_setting(db, "lan_url", str(body.lan_url).rstrip("/") if body.lan_url else None)
    await set_setting(db, "remote_url", str(body.remote_url).rstrip("/") if body.remote_url else None)
    await audit(db, p.actor, "settings_updated")
    await db.commit()
    return await get_settings_(db, p)


@router.post("/system/safe-mode/release")
async def release_safe_mode(db: DB, p: Admin) -> dict:
    """User confirms device matching after restore/move; commands are allowed again."""
    await set_setting(db, "safe_mode", False)
    await audit(db, p.actor, "safe_mode_released")
    await db.commit()
    return {"safe_mode": False}


def backup_view(b: BackupRecord) -> dict:
    return {
        "id": b.id,
        "created_at": b.created_at,
        "kind": b.kind,
        "filename": b.filename,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "checksum": b.sha256,
        "status": b.status,
        "detail": b.detail,
    }


@router.get("/system/backups")
async def backups(db: DB, p: Admin) -> list[dict]:
    rows = (
        await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(50))
    ).scalars()
    return [backup_view(b) for b in rows]


class BackupIn(BaseModel):
    passphrase: str = Field(min_length=bk.MIN_PASSPHRASE, max_length=256)
    include_secrets: bool = True


async def make_backup(db, passphrase: str, include_secrets: bool, kind: str = "export") -> BackupRecord:
    s = get_settings()
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    z2m = s.data_dir / "zigbee2mqtt"
    blob, manifest = await bk.export_backup(db, passphrase, include_secrets, z2m if z2m.is_dir() else None)
    name = f"shc-{datetime.now(UTC):%Y%m%d-%H%M%S}.shcbak"
    path = s.backup_dir / name
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)
    # verify what was written can be opened (restore test light)
    bk.open_archive(path.read_bytes(), passphrase)
    rec = BackupRecord(
        kind=kind,
        filename=name,
        size_bytes=len(blob),
        sha256=hashlib.sha256(blob).hexdigest(),
        status="ok",
        detail={
            "tables": {k: v["rows"] for k, v in manifest["tables"].items()},
            "secrets_included": include_secrets,
            "zigbee2mqtt_included": manifest["zigbee2mqtt_included"],
        },
    )
    db.add(rec)
    return rec


@router.post("/system/backups")
async def create_backup(body: BackupIn, db: DB, p: Admin) -> dict:
    try:
        rec = await make_backup(db, body.passphrase, body.include_secrets)
    except bk.BackupError as e:
        raise api_error(500, "backup_failed", str(e)) from e
    await audit(db, p.actor, "backup_created", rec.filename)
    await db.commit()
    return backup_view(rec)


@router.get("/system/backups/{backup_id}/download")
async def download(backup_id: uuid.UUID, db: DB, p: Admin):
    b = await db.get(BackupRecord, backup_id)
    if not b:
        raise not_found("Back-up")
    path = get_settings().backup_dir / b.filename
    if not path.is_file() or path.parent != get_settings().backup_dir:
        raise not_found("Back-upbestand")
    return FileResponse(path, filename=b.filename, media_type="application/octet-stream")


@router.post("/homes/{home_id}/archive")
async def archive_home(home_id: uuid.UUID, db: DB, p: Admin) -> dict:
    """Moving out: the old home and its history stay, but become read-only/inactive."""
    h = await db.get(Home, home_id)
    if not h:
        raise not_found("Woning")
    h.active_to = datetime.now(UTC)
    await audit(db, p.actor, "home_archived", h.name)
    await db.commit()
    return views.home_view(h)


_ = (Request, timedelta)
