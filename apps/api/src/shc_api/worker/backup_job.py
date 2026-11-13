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
- An interrupted attempt counts as a failed one: ``backup_state.running_since`` without a result
  means the worker (or its container) stopped while the backup process ran. ``backup_due`` treats it
  as a failure, and the worker records it at start and in every backup round in which it runs no
  backup itself (``recover_interrupted_backup``): failures + 1 with the same back-off, error
  "Back-up onderbroken (worker herstart)", automatic backup rows still "running" become "failed" and
  the half-written temporary file is removed.
- Only automatic backups are pruned, file and row together; manual backups are never removed.
- Schedule "dump_daily_full_weekly" (optional, item 32): on most days an encrypted database dump
  (pg_dump, ``shc-api backup --auto --dump``, quick and small) and once a week the full export with
  the Zigbee2MQTT data. The default "daily_full" makes the full export every day. Dumps and exports
  are pruned separately, each keeping the newest ``keep``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import sessionmaker
from ..logfilter import fouttekst
from ..models import BackupRecord, Home
from ..store import get_secret_setting, get_setting, raise_alert, resolve_alert, set_setting
from ..views.status import publish_status

log = logging.getLogger("shc.worker")  # same logger as core.log

ALERT_KEY = "backup_failed"
CATCH_UP_AFTER = timedelta(hours=23)
SCHEDULES = ("daily_full", "dump_daily_full_weekly")
FULL_EVERY = timedelta(days=7)
FULL_SLACK = timedelta(hours=12)  # a weekly export at 03:00 may start a little before 7 x 24 h
MAX_BACKOFF = timedelta(hours=6)
BACKUP_TIMEOUT_S = 6 * 3600
STALE_RUNNING = timedelta(hours=8)
INTERRUPTED_ERROR = "Back-up onderbroken (worker herstart)"
# The backup child process volunteers as the first victim when the container runs out of memory, so an
# OOM inside the api/worker container kills the backup, not the worker or the API (infra request, C3).
OOM_SCORE_ADJ = 500
OOM_SCORE_ADJ_FILE = Path("/proc/self/oom_score_adj")


def prefer_oom_kill(score: int = OOM_SCORE_ADJ, path: Path = OOM_SCORE_ADJ_FILE) -> bool:
    """Best effort: raise this process's ``oom_score_adj`` to ``score`` (never lowers it; raising needs
    no privileges). Returns True when the value is (now) at least ``score``."""
    try:
        current = int(path.read_text().strip() or 0)
        if current >= score:
            return True
        path.write_text(f"{score}\n")
        return True
    except (OSError, ValueError) as exc:
        log.debug("oom_score_adj niet aangepast: %s", exc)
        return False


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
        "schedule": stored.get("schedule") if stored.get("schedule") in SCHEDULES else "daily_full",
        "timezone": await home_timezone(db),
    }


def _ts(v) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _parse_ts(v) -> datetime | None:
    """Like ``_ts``, but None for anything that is not an ISO timestamp (hand-edited setting)."""
    try:
        t = datetime.fromisoformat(v) if isinstance(v, str) and v else None
    except ValueError:
        return None
    return t.replace(tzinfo=UTC) if t is not None and t.tzinfo is None else t


async def last_auto_times(db) -> tuple[datetime | None, datetime | None]:
    """(last automatic full export, last automatic database dump)."""
    return _ts(await get_setting(db, "last_auto_backup")), _ts(await get_setting(db, "last_auto_dump"))


def latest(*times: datetime | None) -> datetime | None:
    present = [t for t in times if t is not None]
    return max(present) if present else None


def auto_kind(now: datetime, schedule: str, last_full: datetime | None) -> str:
    """What the next automatic backup is: "full" (export with Zigbee2MQTT data) or "dump"."""
    if schedule != "dump_daily_full_weekly":
        return "full"
    if last_full is None or now - last_full >= FULL_EVERY - FULL_SLACK:
        return "full"
    return "dump"


# --- schedule -----------------------------------------------------------------------------------


def last_slot(now: datetime, hour: int, tz: ZoneInfo) -> datetime:
    """The most recent scheduled moment (today at `hour` local, or yesterday when that is still ahead)."""
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), time(hour), tz)
    if slot > local:
        slot = datetime.combine(local.date() - timedelta(days=1), time(hour), tz)
    return slot.astimezone(UTC)


def interrupted_since(state: dict, last_auto: datetime | None = None) -> datetime | None:
    """Start of an automatic backup that has no result: ``running_since`` is set when the backup
    process starts and dropped when its result is recorded. None when nothing is pending, or when an
    automatic backup newer than that start exists (``last_auto``): the process did finish and only
    the worker's bookkeeping was lost."""
    since = _parse_ts(state.get("running_since")) if isinstance(state, dict) else None
    if since is None or (last_auto is not None and last_auto >= since):
        return None
    return since


def interrupted_state(state: dict, since: datetime) -> dict:
    """``state`` with the interrupted attempt counted as a failed one (same shape as a recorded failure)."""
    return {
        "last_attempt": since.isoformat(),
        "last_error": INTERRUPTED_ERROR,
        "failures": int(state.get("failures", 0) or 0) + 1,
        "last_ok": state.get("last_ok"),
    }


def effective_state(state: dict, last_auto: datetime | None = None) -> dict:
    """The backup state as the schedule sees it: an interrupted attempt counts as failed."""
    since = interrupted_since(state, last_auto)
    return interrupted_state(state, since) if since is not None else state


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
    and before the most recent slot; failed attempts back off instead of retrying immediately. An
    attempt that was interrupted (``running_since`` without a result) counts as a failed one."""
    state = effective_state(state, last_auto)
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
    state = effective_state(state, last_auto)
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


async def run_auto_backup(dump: bool = False) -> dict:
    """Make an automatic backup (``dump``: an encrypted database dump instead of the full export) and
    prune old automatic ones of the same kind. Returns a result dict for the worker."""
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
        rec = await (bk.run_db_dump(pw, auto=True) if dump else bk.run_backup(pw, True, auto=True))
    except bk.BackupError as e:
        return {"status": "failed", "error": str(e)}
    pruned = await prune_auto_backups(cfg["keep"], s, kind=bk.DUMP_KIND if dump else "export")
    async with sessionmaker()() as db:
        await set_setting(db, "last_auto_dump" if dump else "last_auto_backup", datetime.now(UTC).isoformat())
        await db.commit()
    return {**rec, "pruned": pruned}


async def prune_auto_backups(keep: int, settings: Settings | None = None, kind: str = "export") -> list[str]:
    """Delete automatic backups of ``kind`` ("export" or "db_dump") beyond the newest `keep` (file and
    row). Manual backups stay."""
    s = settings or get_settings()
    keep = max(1, keep)
    removed = []
    async with sessionmaker()() as db:
        rows = (
            (
                await db.execute(
                    select(BackupRecord)
                    .where(BackupRecord.auto.is_(True), BackupRecord.status == "ok", BackupRecord.kind == kind)
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
    """Run `shc-api backup ... --json` in a separate process; returns its JSON result (or an error).
    The child raises its own oom_score_adj first (``prefer_oom_kill``, called by the CLI)."""
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


def remove_partial_auto_files(backup_dir: Path) -> int:
    """Delete the temporary files of automatic backups that never finished (``.shc-auto-*.tmp``; a
    finished backup is renamed to its final name). Only called when no backup process runs."""
    n = 0
    for p in backup_dir.glob(".shc-auto-*.tmp"):
        try:
            p.unlink()
            n += 1
        except OSError as exc:
            log.warning("Tijdelijk back-upbestand %s niet verwijderd: %s", p.name, exc)
    return n


class BackupMixin:
    # Provided by core.Worker.
    settings: Settings

    async def backup_round(self) -> None:
        """Start an automatic backup when one is due. Returns immediately; the backup runs in its
        own process, watched by a background task, so the loop (and the liveness check) never wait."""
        task = getattr(self, "_backup_task", None)
        if task is not None and not task.done():
            return
        # No backup process of ours runs now: a running_since left behind is an interrupted attempt.
        await self.recover_interrupted_backup()
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            # Never raises on a passphrase that does not decrypt (restore on a new NAS, key change):
            # that is reported as passphrase_error, so this loop keeps running (verbeterlijst 22/32).
            cfg = await load_backup_settings(db, self.settings)
            if not cfg["enabled"]:
                if cfg["passphrase_error"] and cfg["wanted"]:
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
            if not int(state.get("failures", 0) or 0) and await resolve_alert(db, ALERT_KEY):
                # No failed attempt pending, so an open alert can only be the passphrase that did not
                # fit the master key: it was set again in the app.
                await db.commit()
            last_full, last_dump = await last_auto_times(db)
        if not backup_due(now, cfg["hour"], ZoneInfo(cfg["timezone"]), latest(last_full, last_dump), state):
            return
        kind = auto_kind(now, cfg["schedule"], last_full)
        async with sessionmaker()() as db:
            await set_setting(db, "backup_state", {**state, "running_since": now.isoformat(), "running_kind": kind})
            await db.commit()
        self._backup_task = asyncio.create_task(self._auto_backup_watch(state, kind))

    async def recover_interrupted_backup(self) -> bool:
        """Record an automatic backup that has no result (the worker or its container stopped while the
        backup process ran) as a failed attempt: failures + 1 with the usual back-off and alert, the
        automatic backup rows still "running" become "failed" and the half-written file is removed.
        Called when the worker starts and in every backup round without a backup of its own running.
        Returns True when something was recorded."""
        task = getattr(self, "_backup_task", None)
        if task is not None and not task.done():
            return False  # our own backup process is still running
        async with sessionmaker()() as db:
            state = await get_setting(db, "backup_state", {}) or {}
            if not isinstance(state, dict) or "running_since" not in state:
                return False
            last_full, last_dump = await last_auto_times(db)
        last_auto = latest(last_full, last_dump)
        started = _parse_ts(state.get("running_since"))
        if started is not None and last_auto is not None and last_auto >= started:
            # The process finished (it records last_auto_*), only the worker stopped before the result.
            await self.record_auto_result({"status": "ok", "filename": "(na herstart vastgesteld)"}, state, started)
            return True
        started = started or datetime.now(UTC)
        async with sessionmaker()() as db:
            rows = (
                (
                    await db.execute(
                        select(BackupRecord).where(BackupRecord.auto.is_(True), BackupRecord.status == "running")
                    )
                )
                .scalars()
                .all()
            )
            for b in rows:
                b.status = "failed"
                b.detail = {**(b.detail or {}), "error": INTERRUPTED_ERROR}
            await db.commit()
        removed = remove_partial_auto_files(self.settings.backup_dir)
        log.warning(
            "Automatische back-up van %s is onderbroken (worker herstart); %d regel(s) op mislukt gezet, "
            "%d tijdelijk bestand(en) verwijderd",
            started.isoformat(),
            len(rows),
            removed,
        )
        await self.record_auto_result({"status": "failed", "error": INTERRUPTED_ERROR}, state, started)
        return True

    async def _auto_backup_watch(self, state: dict, kind: str = "full") -> dict:
        started = datetime.now(UTC)
        try:
            result = await run_backup_process(["--auto", *(["--dump"] if kind == "dump" else [])])
        except Exception as exc:  # spawning failed
            result = {"status": "failed", "error": f"Back-upproces kon niet starten: {fouttekst(exc)}"}
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
                sep = " " if err.endswith((".", "!", "?")) else ". "
                await raise_alert(
                    db,
                    home_id,
                    ALERT_KEY,
                    "error",
                    "Automatische back-up mislukt",
                    f"{err}{sep}Volgende poging rond {nxt:%H:%M} UTC." if nxt else err,
                    "backup",
                )
                log.warning("Automatische back-up mislukt (%d): %s", failures, err)
            await set_setting(db, "backup_state", new_state)
            await db.flush()
            # The "Back-up" row of the overview changed: open screens show it at once (item 36).
            await publish_status(db)
            await db.commit()
