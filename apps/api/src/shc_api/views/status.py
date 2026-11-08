"""Status checks and open alerts (overview, System page)."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import backup as bk
from ..config import get_settings
from ..models import Alert, BackupRecord, Integration
from ..store import alert_view, get_setting


async def status_checks(db: AsyncSession, home_id: uuid.UUID | None = None) -> list[dict]:
    q = select(Integration)
    if home_id:
        q = q.where(Integration.home_id == home_id)
    labels = {"hue": "Hue Bridge", "gree": "Gree airco", "homewizard": "HomeWizard", "zigbee": "Zigbee"}
    out = []
    for i in (await db.execute(q)).scalars():
        out.append(
            {
                "key": f"integration:{i.id}",
                "label": labels.get(i.provider, i.name),
                "state": i.state if i.state in ("ok", "warn", "error") else "unknown",
                "detail": i.detail or i.name,
            }
        )
    return out


async def open_alerts(db: AsyncSession, home_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = (
        await db.execute(
            select(Alert)
            .where(Alert.home_id == home_id, Alert.resolved_at.is_(None))
            .order_by(Alert.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [alert_view(a) for a in rows]


async def system_checks(db: AsyncSession) -> list[dict]:
    """All checks for `GET /system/status`: database, worker, disk, backup, clock, safe mode, integrations."""
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
        checks.append({"key": "worker", "label": "Achtergronddienst", "state": "error", "detail": "Geen hartslag"})
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
            select(BackupRecord).where(BackupRecord.status == "ok").order_by(BackupRecord.created_at.desc()).limit(1)
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
        checks.append({"key": "backup", "label": "Back-up", "state": "warn", "detail": "Nog geen back-up gemaakt"})
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
    checks += await status_checks(db)
    return checks
