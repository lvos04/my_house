"""API-side watch on the worker heartbeat and the backups (verbeterlijst 22, 28 and 36).

The worker cannot report its own absence, and a backup that silently stops changes no row anyone
listens to. So every API process runs ``StatusWatch``: once a minute one of them (advisory lock)
compares the overview checks "Achtergronddienst" and "Back-up" with the previous round (Setting
``status_watch``, so a restart of the API does not repeat anything) and on a change

- raises or resolves the "achtergronddienst reageert niet" alert of every active home,
- publishes ``status.updated`` so open screens (web, Android) show it at once, and
- sends the optional push message outside the app (``notify``), only when the worker goes down or
  comes back, or the backups start or stop failing / getting too old.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import notify
from .db import sessionmaker
from .models import Home
from .store import get_setting, set_setting
from .views.status import BACKUP_WARN_H, WORKER_ALERT_S, backup_check, publish_status, sync_worker_alert, worker_check

log = logging.getLogger(__name__)

WATCH_KEY = "status_watch"
WATCH_LOCK = 42_4244  # advisory lock: one API process per round
INTERVAL_S = 60.0
# First round only after the worker had time to start too (host reboot: both containers come up
# together, and the heartbeat from before the reboot is stale).
FIRST_AFTER_S = float(WORKER_ALERT_S)


def _worker_down(check: dict) -> bool:
    """Down for the push message: no heartbeat for longer than the container healthcheck allows."""
    if check["state"] != "error":
        return False  # ok, or maintenance (warn)
    age = check.get("age_s")
    return age is None or age > WORKER_ALERT_S


def _backup_problem(check: dict) -> bool:
    """Automatic backups fail, or the last good backup is too old (or there is none)."""
    if check.get("auto_failures"):
        return True
    age = check.get("age_h")
    return age is None or age >= BACKUP_WARN_H


def _messages(prev: dict, cur: dict, worker: dict, backup: dict) -> list[tuple[str, str, str, str]]:
    """(title, body, priority, tags) for every push-worthy change since the previous round."""
    out = []
    # A problem is reported also in the very first round (nothing known yet); a recovery only after
    # a reported problem.
    if bool(prev.get("worker_down")) != cur["worker_down"]:
        if cur["worker_down"]:
            out.append(
                (
                    "Smart Home: achtergronddienst reageert niet",
                    worker["detail"] + " Er komen ook geen back-ups en geen meldingen in de app. Controleer de "
                    "worker-container (logboek, herstart).",
                    "high",
                    "warning",
                )
            )
        else:
            out.append(
                ("Smart Home: achtergronddienst draait weer", worker["detail"] + ".", "default", "white_check_mark")
            )
    if bool(prev.get("backup_problem")) != cur["backup_problem"]:
        if cur["backup_problem"]:
            out.append(
                (
                    "Smart Home: back-ups lopen niet",
                    backup["detail"] + ". Controleer Systeem › Back-ups.",
                    "high",
                    "warning",
                )
            )
        else:
            out.append(("Smart Home: back-ups lopen weer", backup["detail"] + ".", "default", "white_check_mark"))
    return out


async def check_once(db: AsyncSession, now: datetime | None = None) -> dict | None:
    """One round. Returns what changed (None when another API process holds the round, or nothing
    changed). Commits. Push messages are sent after the commit, outside the transaction."""
    now = now or datetime.now(UTC)
    if not (await db.execute(select(func.pg_try_advisory_xact_lock(WATCH_LOCK)))).scalar():
        return None
    prev = await get_setting(db, WATCH_KEY, {}) or {}
    worker = await worker_check(db, now)
    backup = await backup_check(db, now)
    cur = {
        "worker": worker["state"],
        "backup": backup["state"],
        "worker_down": _worker_down(worker),
        "backup_problem": _backup_problem(backup),
    }
    changed = sorted(k for k in cur if prev.get(k) != cur[k])
    if not changed:
        await db.rollback()
        return None
    await set_setting(db, WATCH_KEY, cur)
    if "worker" in changed or "worker_down" in changed:
        for home_id in (await db.execute(select(Home.id).where(Home.active_to.is_(None)))).scalars().all():
            await sync_worker_alert(db, home_id)
    if "worker" in changed or "backup" in changed:
        await publish_status(db)
    messages = _messages(prev, cur, worker, backup)
    url, _source = await notify.configured_url(db) if messages else (None, None)
    await db.commit()
    sent = []
    for title, body, priority, tags in messages if url else []:
        error = await notify.send(url, title, body, priority=priority, tags=tags)
        sent.append({"title": title, "error": error})
    return {"changed": changed, "state": cur, "notified": sent}


class StatusWatch:
    """Background task of the API process (started in main.lifespan)."""

    def __init__(self, interval_s: float = INTERVAL_S, first_after_s: float = FIRST_AFTER_S) -> None:
        self.interval_s = interval_s
        self.first_after_s = first_after_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="status-watch")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        delay = self.first_after_s
        while True:
            await asyncio.sleep(delay)
            delay = self.interval_s
            try:
                async with sessionmaker()() as db:
                    await check_once(db)
            except asyncio.CancelledError:
                raise
            except Exception:  # a database hiccup must not end the watch
                log.warning("Statuscontrole (worker/back-up) mislukt", exc_info=True)


watch = StatusWatch()
