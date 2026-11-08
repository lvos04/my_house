"""Scheduled automatic backups (verbeterlijst 5 and 32).

- Settings live in the app (GET/PUT /system/backup-settings): passphrase (stored encrypted with the
  master key), hour of day in the home's time zone, how many automatic backups to keep. The old
  ``<data_dir>/backup_passphrase`` file still works as a fallback.
- Catch-up: a backup runs at the first opportunity after the configured hour when the last automatic
  backup is older than 23 hours (NAS off at night -> backup when it is back).
- The backup itself runs in a separate process (``shc-api backup --auto``), so the worker never holds
  backup data in memory and its loops keep running; the worker loop only starts it and returns.
- A failed attempt is recorded (Setting "backup_state") and retried with back-off (1 h, 2 h, ... max
  6 h), never immediately; it raises an alert, which is resolved by the next successful backup.
- Only automatic backups are pruned, file and row together; manual backups are never removed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import sessionmaker
from ..models import BackupRecord, Home
from ..store import get_secret_setting, get_setting, raise_alert, resolve_alert, set_setting

log = logging.getLogger("shc.worker")  # same logger as core.log

ALERT_KEY = "backup_failed"
CATCH_UP_AFTER = timedelta(hours=23)
MAX_BACKOFF = timedelta(hours=6)
BACKUP_TIMEOUT_S = 6 * 3600
STALE_RUNNING = timedelta(hours=8)


# --- settings -----------------------------------------------------------------------------------


async def home_timezone(db) -> str:
    tz = (
        await db.execute(select(Home.timezone).where(Home.active_to.is_(None)).order_by(Home.active_from).limit(1))
    ).scalar_one_or_none()
    return tz or "UTC"


async def primary_home_id(db):
    return (
        await db.execute(select(Home.id).where(Home.active_to.is_(None)).order_by(Home.active_from).limit(1))
    ).scalar_one_or_none()


async def backup_passphrase(db, settings: Settings | None = None) -> tuple[str | None, str | None]:
    """(passphrase, source) where source is "app" or "file"; (None, None) when not configured.
    Raises ValueError when the stored passphrase can not be decrypted with the current key."""
    p = await get_secret_setting(db, "backup_passphrase_enc")
    if p:
        return p, "app"
    s = settings or get_settings()
    pf = s.data_dir / "backup_passphrase"
    if pf.is_file():
        v = pf.read_text().strip()
        if v:
            return v, "file"
    return None, None


async def load_backup_settings(db, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    stored = await get_setting(db, "backup_settings", {}) or {}
    try:
        pw, source = await backup_passphrase(db, s)
        key_error = None
    except ValueError:
        pw, source, key_error = None, "app", "De opgeslagen wachtwoordzin past niet bij de huidige hoofdsleutel."
    return {
        "enabled": bool(stored.get("enabled", True)) and pw is not None,
        "wanted": bool(stored.get("enabled", True)),
        "passphrase_set": pw is not None,
        "passphrase_source": source,
        "passphrase_error": key_error,
        "hour": int(stored.get("hour", s.backup_hour_local)),
        "keep": int(stored.get("keep", s.backup_keep)),
        "timezone": await home_timezone(db),
    }


# --- schedule -----------------------------------------------------------------------------------


def last_slot(now: datetime, hour: int, tz: ZoneInfo) -> datetime:
    """The most recent scheduled moment (today at `hour` local, or yesterday when that is still ahead)."""
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), time(hour), tz)
    if slot > local:
        slot = datetime.combine(local.date() - timedelta(days=1), time(hour), tz)
    return slot.astimezone(UTC)


def retry_after(state: dict) -> datetime | None:
    """When the next attempt may start after failures (None: no failure pending)."""
    failures = int(state.get("failures", 0) or 0)
    last = state.get("last_attempt")
    if not failures or not last:
        return None
    wait = min(MAX_BACKOFF, timedelta(hours=2 ** (failures - 1)))
    return datetime.fromisoformat(last) + wait


def backup_due(now: datetime, hour: int, tz: ZoneInfo, last_auto: datetime | None, state: dict) -> bool:
    """Due at the first opportunity after the hour, when the last automatic backup is older than 23 h
    and before the most recent slot; failed attempts back off instead of retrying immediately."""
    ra = retry_after(state)
    if ra and now < ra:
        return False
    if last_auto is None:
        return True
    if now - last_auto < CATCH_UP_AFTER:
        return False
    return last_auto < last_slot(now, hour, tz)


def next_due(now: datetime, hour: int, tz: ZoneInfo, last_auto: datetime | None, state: dict) -> datetime:
    """Best estimate of the next automatic backup (for display)."""
    if backup_due(now, hour, tz, last_auto, state):
        return now
    if last_auto is None:
        t = now
    else:
        t = max(now, last_auto + CATCH_UP_AFTER)
        if not last_auto < last_slot(t, hour, tz):
            local = t.astimezone(tz)
            slot = datetime.combine(local.date(), time(hour), tz)
            if slot <= local:
                slot = datetime.combine(local.date() + timedelta(days=1), time(hour), tz)
            t = slot.astimezone(UTC)
    ra = retry_after(state)
    return max(t, ra) if ra else t


# --- the backup itself (runs in the `shc-api backup --auto` process) ---------------------------------


async def run_auto_backup() -> dict:
    """Make an automatic backup and prune old automatic ones. Returns a result dict for the worker."""
    from .. import backup as bk

    s = get_settings()
    async with sessionmaker()() as db:
        try:
            pw, _source = await backup_passphrase(db, s)
        except ValueError:
            return {"status": "failed", "error": "De opgeslagen wachtwoordzin past niet bij de huidige hoofdsleutel."}
        cfg = await load_backup_settings(db, s)
    if not pw:
        return {"status": "failed", "error": "Geen wachtwoordzin voor automatische back-ups ingesteld."}
    try:
        rec = await bk.run_backup(pw, True, auto=True)
    except bk.BackupError as e:
        return {"status": "failed", "error": str(e)}
    pruned = await prune_auto_backups(cfg["keep"], s)
    async with sessionmaker()() as db:
        await set_setting(db, "last_auto_backup", datetime.now(UTC).isoformat())
        await db.commit()
    return {**rec, "pruned": pruned}


async def prune_auto_backups(keep: int, settings: Settings | None = None) -> list[str]:
    """Delete automatic backups beyond the newest `keep` (file and row). Manual backups stay."""
    s = settings or get_settings()
    keep = max(1, keep)
    removed = []
    async with sessionmaker()() as db:
        rows = (
            (
                await db.execute(
                    select(BackupRecord)
                    .where(BackupRecord.auto.is_(True), BackupRecord.status == "ok")
                    .order_by(BackupRecord.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for b in rows[keep:]:
            p = s.backup_dir / b.filename
            if p.parent == s.backup_dir:
                p.unlink(missing_ok=True)
            removed.append(b.filename)
            await db.delete(b)
        # failed automatic attempts older than 30 days: only the row (there is no file)
        old_failed = (
            (
                await db.execute(
                    select(BackupRecord).where(
                        BackupRecord.auto.is_(True),
                        BackupRecord.status == "failed",
                        BackupRecord.created_at < datetime.now(UTC) - timedelta(days=30),
                    )
                )
            )
            .scalars()
            .all()
        )
        for b in old_failed:
            await db.delete(b)
        await db.commit()
    return removed


async def run_backup_process(args: list[str], stdin: bytes | None = None, limit_s: float = BACKUP_TIMEOUT_S) -> dict:
    """Run `shc-api backup ... --json` in a separate process; returns its JSON result (or an error)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "shc_api.cli",
        "backup",
        *args,
        "--json",
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=limit_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"status": "failed", "error": "De back-up duurde te lang en is afgebroken."}
    result = None
    for line in reversed(out.decode(errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                result = json.loads(line)
                break
            except ValueError:
                continue
    if result is None:
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or [f"afsluitcode {proc.returncode}"]
        result = {"status": "failed", "error": f"Back-upproces gestopt: {tail[0][:300]}"}
    elif proc.returncode != 0 and result.get("status") == "ok":
        result["status"] = "failed"
    return result


class BackupMixin:
    # Provided by core.Worker.
    settings: Settings

    async def backup_round(self) -> None:
        """Start an automatic backup when one is due. Returns immediately; the backup runs in its
        own process, watched by a background task, so the loop (and the liveness check) never wait."""
        task = getattr(self, "_backup_task", None)
        if task is not None and not task.done():
            return
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            cfg = await load_backup_settings(db, self.settings)
            if not cfg["enabled"]:
                if cfg["passphrase_error"]:
                    await raise_alert(
                        db,
                        await primary_home_id(db),
                        ALERT_KEY,
                        "error",
                        "Automatische back-up kan niet",
                        cfg["passphrase_error"] + " Stel de wachtwoordzin opnieuw in bij Systeem.",
                        "backup",
                    )
                    await db.commit()
                return
            state = await get_setting(db, "backup_state", {}) or {}
            last = await get_setting(db, "last_auto_backup")
        last_auto = datetime.fromisoformat(last) if last else None
        if not backup_due(now, cfg["hour"], ZoneInfo(cfg["timezone"]), last_auto, state):
            return
        async with sessionmaker()() as db:
            await set_setting(db, "backup_state", {**state, "running_since": now.isoformat()})
            await db.commit()
        self._backup_task = asyncio.create_task(self._auto_backup_watch(state))

    async def _auto_backup_watch(self, state: dict) -> dict:
        started = datetime.now(UTC)
        try:
            result = await run_backup_process(["--auto"])
        except Exception as exc:  # spawning failed
            result = {"status": "failed", "error": f"Back-upproces kon niet starten: {exc}"}
        await self.record_auto_result(result, state, started)
        return result

    async def record_auto_result(self, result: dict, state: dict, started: datetime) -> None:
        async with sessionmaker()() as db:
            home_id = await primary_home_id(db)
            if result.get("status") == "ok":
                new_state = {
                    "last_attempt": started.isoformat(),
                    "last_ok": datetime.now(UTC).isoformat(),
                    "failures": 0,
                }
                await resolve_alert(db, ALERT_KEY)
                log.info("Automatische back-up: %s", result.get("filename"))
            else:
                failures = int(state.get("failures", 0) or 0) + 1
                err = str(result.get("error") or "onbekende fout")[:500]
                new_state = {
                    "last_attempt": started.isoformat(),
                    "last_error": err,
                    "failures": failures,
                    "last_ok": state.get("last_ok"),
                }
                nxt = retry_after(new_state)
                await raise_alert(
                    db,
                    home_id,
                    ALERT_KEY,
                    "error",
                    "Automatische back-up mislukt",
                    f"{err} Volgende poging rond {nxt:%H:%M} UTC." if nxt else err,
                    "backup",
                )
                log.warning("Automatische back-up mislukt (%d): %s", failures, err)
            await set_setting(db, "backup_state", new_state)
            await db.commit()
