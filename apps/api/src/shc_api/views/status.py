"""Status checks (overview, ``status.updated``, System page) and open alerts.

The overview status (``status_checks``) has the worker heartbeat, safe mode, the age of the last
backup and every integration (verbeterlijst 22); ``system_checks`` adds database, disk, clock,
maintenance, stuck worker loops and per-integration health details (MQTT and USB dongle apart for
Zigbee, missed polls, eventstream connection; verbeterlijst 28).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import backup as bk
from ..config import get_settings
from ..models import Alert, BackupRecord, Home, Integration
from ..store import alert_view, get_setting, raise_alert, resolve_alert

PROVIDER_LABELS = {"hue": "Hue Bridge", "gree": "Gree airco", "homewizard": "HomeWizard", "zigbee": "Zigbee"}
WORKER_OK_S = 120  # heartbeat every 30 s
WORKER_ALERT_S = 180  # same limit as the container healthcheck (contract C3)
BACKUP_WARN_H = 36
STATES = ("ok", "warn", "error")


def _age_s(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (now - dt).total_seconds()


def _human_age(seconds: float) -> str:
    if seconds < 120:
        return f"{int(seconds)} s"
    if seconds < 7200:
        return f"{int(seconds // 60)} min"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} uur"
    return f"{seconds / 86400:.0f} dagen"


def _hhmm(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(UTC).strftime("%d-%m %H:%M UTC")
    except ValueError:
        return None


async def worker_check(db: AsyncSession, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if await get_setting(db, "maintenance", False):
        return {
            "key": "worker",
            "label": "Achtergronddienst",
            "state": "warn",
            "detail": "Onderhoudsmodus: de achtergronddienst is gepauzeerd (herstel loopt).",
            "age_s": None,
        }
    age = _age_s(await get_setting(db, "worker_heartbeat"), now)
    if age is None:
        return {
            "key": "worker",
            "label": "Achtergronddienst",
            "state": "error",
            "detail": "Geen hartslag: de achtergronddienst draait niet. Apparaten worden niet uitgelezen en "
            "de klimaatregeling staat stil.",
            "age_s": None,
        }
    ok = age < WORKER_OK_S
    detail = f"Laatste hartslag {_human_age(max(age, 0))} geleden"
    if not ok:
        detail += ". Apparaten worden niet uitgelezen en de klimaatregeling staat stil."
    return {
        "key": "worker",
        "label": "Achtergronddienst",
        "state": "ok" if ok else "error",
        "detail": detail,
        "age_s": int(age),
    }


async def sync_worker_alert(db: AsyncSession, home_id: uuid.UUID) -> None:
    """Raise/resolve "achtergronddienst reageert niet" for a home (the API side of verbeterlijst 22).
    The worker resolves it itself as soon as it beats again. Does not commit."""
    if await get_setting(db, "maintenance", False):
        return
    age = _age_s(await get_setting(db, "worker_heartbeat"), datetime.now(UTC))
    key = f"worker_heartbeat:{home_id}"
    if age is None or age > WORKER_ALERT_S:
        await raise_alert(
            db,
            home_id,
            key,
            "error",
            "Achtergronddienst reageert niet",
            ("Nog nooit een hartslag ontvangen." if age is None else f"Laatste hartslag {_human_age(age)} geleden.")
            + " Apparaten worden niet uitgelezen, de klimaatregeling staat stil en er komen geen back-ups. "
            "Controleer de worker-container (logboek, herstart).",
            "system",
        )
    else:
        await resolve_alert(db, key)


async def backup_check(db: AsyncSession, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    last = (
        await db.execute(
            select(BackupRecord).where(BackupRecord.status == "ok").order_by(BackupRecord.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return {
            "key": "backup",
            "label": "Back-up",
            "state": "warn",
            "detail": "Nog geen back-up gemaakt",
            "age_h": None,
        }
    age_h = (now - last.created_at).total_seconds() / 3600
    return {
        "key": "backup",
        "label": "Back-up",
        "state": "ok" if age_h < BACKUP_WARN_H else "warn",
        "detail": f"Laatste back-up {_human_age(age_h * 3600)} geleden ({last.created_at:%d-%m %H:%M} UTC)",
        "age_h": round(age_h, 1),
    }


async def safe_mode_check(db: AsyncSession) -> dict | None:
    if not await get_setting(db, "safe_mode", False):
        return None
    return {
        "key": "safe_mode",
        "label": "Veilige modus",
        "state": "warn",
        "detail": "Geen apparaatcommando's tot je de koppelingen bevestigt.",
    }


def _label(i: Integration, counts: dict[str, int]) -> str:
    base = PROVIDER_LABELS.get(i.provider, i.name)
    if counts.get(i.provider, 0) > 1 and i.name and i.name != base:
        return f"{base} – {i.name}"
    return base


def zigbee_parts(i: Integration, extra: dict) -> list[dict]:
    """MQTT connection and USB dongle (Zigbee2MQTT bridge) as two separate checks."""
    mqtt = extra.get("mqtt_connected")
    since = _hhmm(extra.get("mqtt_connected_since"))
    if mqtt is True:
        m_state, m_detail = "ok", "Verbonden" + (f" sinds {since}" if since else "")
    elif mqtt is False:
        err = extra.get("mqtt_error")
        m_state = "error"
        m_detail = "Geen verbinding met de MQTT-broker (mosquitto)" + (f": {err}" if err else "")
    else:
        m_state, m_detail = "unknown", "Nog geen status van de achtergronddienst"
    bridge = extra.get("bridge_online")
    if mqtt is False:
        d_state, d_detail = "unknown", "Onbekend zolang MQTT niet verbonden is"
    elif bridge is True:
        coord = extra.get("coordinator_type")
        n = extra.get("devices")
        d_state = "ok"
        d_detail = "Zigbee2MQTT online" + (f", coördinator {coord}" if coord else "")
        if n is not None:
            d_detail += f", {n} apparaten"
    elif bridge is False:
        d_state = "error"
        d_detail = "Zigbee2MQTT meldt offline: controleer of de USB-dongle is aangesloten."
    else:
        d_state, d_detail = "unknown", "Zigbee2MQTT meldt zich niet (draait de Zigbee2MQTT-container?)"
    return [
        {"key": f"integration:{i.id}:mqtt", "label": "Zigbee – MQTT-verbinding", "state": m_state, "detail": m_detail},
        {"key": f"integration:{i.id}:dongle", "label": "Zigbee – USB-dongle", "state": d_state, "detail": d_detail},
    ]


def integration_details(i: Integration) -> dict:
    """Health details the worker stored for an integration (no secrets)."""
    diag = i.diagnostics or {}
    health = diag.get("health") or {}
    status = diag.get("status") or {}
    extra = health.get("extra") or {}
    polls = diag.get("polls") or {}
    return {
        "code": status.get("code") or health.get("code"),
        "since": status.get("since"),
        "health_state": health.get("state"),
        "health_detail": health.get("detail"),
        "checked_at": health.get("checked_at"),
        "connected_since": extra.get("connected_since") or extra.get("mqtt_connected_since"),
        "missed_polls": polls.get("missed", status.get("missed_polls", 0)),
        "consecutive_failures": polls.get("consecutive_failures", status.get("consecutive_poll_failures", 0)),
        "extra": extra,
    }


async def integration_checks(db: AsyncSession, home_id: uuid.UUID | None, detailed: bool) -> list[dict]:
    q = (
        select(Integration)
        .join(Home, Home.id == Integration.home_id)
        .where(Integration.enabled.is_(True), Home.active_to.is_(None))
        .order_by(Integration.provider, Integration.name)
    )
    if home_id:
        q = q.where(Integration.home_id == home_id)
    rows = list((await db.execute(q)).scalars())
    counts: dict[str, int] = {}
    for i in rows:
        counts[i.provider] = counts.get(i.provider, 0) + 1
    out = []
    for i in rows:
        det = integration_details(i)
        entry = {
            "key": f"integration:{i.id}",
            "label": _label(i, counts),
            "state": i.state if i.state in STATES else "unknown",
            "detail": i.detail or i.name,
            "integration_id": i.id,
            "provider": i.provider,
            "code": det["code"],
        }
        if detailed:
            entry["details"] = {k: v for k, v in det.items() if k != "extra"}
            entry["details"]["extra"] = det["extra"]
        out.append(entry)
        if i.provider == "zigbee" and (detailed or det["extra"]):
            parts = zigbee_parts(i, det["extra"])
            if detailed:
                out.extend(parts)
            else:
                entry["detail"] = " · ".join(f"{p['label'].split('– ')[-1]}: {p['detail']}" for p in parts)
    return out


async def status_checks(db: AsyncSession, home_id: uuid.UUID | None = None) -> list[dict]:
    """``Overview.status`` and the payload of ``status.updated``."""
    now = datetime.now(UTC)
    out = [await worker_check(db, now)]
    sm = await safe_mode_check(db)
    if sm:
        out.append(sm)
    out.append(await backup_check(db, now))
    out += await integration_checks(db, home_id, detailed=False)
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


def _loop_label(name: str) -> str:
    return {
        "heartbeat": "hartslag",
        "commands": "commando's",
        "climate": "klimaatregeling",
        "health": "gezondheidscontrole",
        "reload": "koppelingen herladen",
        "backup": "back-up",
        "aggregate": "energietotalen",
        "retention": "opruimen",
        "live": "live-vermogen",
    }.get(name, name)


async def loops_check(db: AsyncSession, now: datetime) -> dict | None:
    report = await get_setting(db, "worker_loops")
    if not isinstance(report, dict) or not report.get("loops"):
        return None
    stuck = []
    for name, info in report["loops"].items():
        age = _age_s(info.get("last"), now)
        interval = float(info.get("interval") or 60)
        if info.get("stuck") or (age is not None and age > 5 * interval + 60):
            stuck.append(f"{_loop_label(name)} (laatst {_human_age(max(age or 0, 0))} geleden)")
    if stuck:
        return {
            "key": "worker_loops",
            "label": "Werklussen",
            "state": "error",
            "detail": "Staat stil: " + ", ".join(stuck) + ". De worker wordt als ongezond gemeld.",
        }
    return {
        "key": "worker_loops",
        "label": "Werklussen",
        "state": "ok",
        "detail": f"{len(report['loops'])} lussen lopen",
    }


async def system_checks(db: AsyncSession) -> list[dict]:
    """All checks for ``GET /system/status``."""
    s = get_settings()
    now = datetime.now(UTC)
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
    checks.append(await worker_check(db, now))
    loops = await loops_check(db, now)
    if loops:
        checks.append(loops)
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
    checks.append(await backup_check(db, now))
    ntp = await get_setting(db, "clock_check")
    if isinstance(ntp, dict):
        checks.append(
            {
                "key": "time",
                "label": "Tijdsynchronisatie",
                "state": ntp.get("state", "unknown"),
                "detail": ntp.get("detail", ""),
            }
        )
    sm = await safe_mode_check(db)
    if sm:
        checks.append(sm)
    if await get_setting(db, "maintenance", False):
        checks.append(
            {
                "key": "maintenance",
                "label": "Onderhoudsmodus",
                "state": "warn",
                "detail": "Herstel of onderhoud loopt; de achtergronddienst is gepauzeerd.",
            }
        )
    checks += await integration_checks(db, None, detailed=True)
    return checks
