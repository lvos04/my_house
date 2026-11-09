"""Worker lifecycle: loop runner, LISTEN, heartbeat, liveness and adapter loading/polling/subscribing.

The concern-specific loops live in the mixins this class is assembled from.

- Every loop runs through ``_forever``: crash-only (exception -> log, back-off, continue), paused
  while the "maintenance" setting is on (restore running, contract C8), and it records its last
  completed iteration in ``loop_seen`` (contract C3).
- ``heartbeat`` writes the ``worker_heartbeat`` / ``worker_loops`` settings and touches
  ``/tmp/shc-worker-alive`` for the container healthcheck, unless a watched loop is stuck.
- A watchdog thread (verbeterlijst 28, "zichzelf herstarten") ends the process with exit code 1 when
  a watched loop is stuck for more than 5 x its interval + 60 s, also when the event loop itself is
  blocked: Docker does not restart an unhealthy container outside Swarm, but ``restart:
  unless-stopped`` does restart one that exited.
- Integrations are loaded one by one (verbeterlijst 22): a key that no longer decrypts marks only
  that integration ("Sleutel past niet, koppel opnieuw"); the rest keeps running. The reload loop
  stops adapters whose integration was disabled, changed or whose home was archived, and starts
  changed ones again (verbeterlijst 24).
- Integration state is debounced (verbeterlijst 28): a transient problem only becomes "error" after
  3 consecutive failures or 5 minutes; one log line per state change; ``status.updated`` on change.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from .. import events
from ..config import get_settings
from ..db import sessionmaker
from ..models import Alert, Device, Home, Integration
from ..security import SecretBox
from ..store import get_setting, raise_alert, resolve_alert, set_setting
from .aggregates import AggregatesMixin
from .backup_job import BackupMixin
from .climate_loop import ClimateMixin
from .commands import CommandsMixin
from .health import HealthMixin
from .ingest import IngestMixin

log = logging.getLogger("shc.worker")

POLL_INTERVAL = {"homewizard": 10, "gree": 30, "hue": 120, "zigbee": 0}
POLL_TIMEOUT_S = 120.0
HEARTBEAT_INTERVAL = 30
RELOAD_INTERVAL = 120
# Liveness file for the container healthcheck (contract C3); stale > 180 s = unhealthy.
ALIVE_FILE = Path(os.environ.get("SHC_WORKER_ALIVE_FILE", "/tmp/shc-worker-alive"))  # noqa: S108
MAINTENANCE_CHECK_S = 10.0
WATCHDOG_CHECK_S = 15.0  # how often the watchdog thread looks for stuck loops
# Debounce for transient problems (verbeterlijst 28).
FAIL_THRESHOLD = 3
FAIL_WINDOW = timedelta(minutes=5)
LAST_OK_WRITE_EVERY = timedelta(seconds=60)
# Problems that do not go away by retrying: reported at once instead of after the debounce.
IMMEDIATE_CODES = frozenset(
    {"identity_mismatch", "certificate_mismatch", "pairing_required", "key_mismatch", "load_failed", "unsupported"}
)
# Device rows are not marked offline for these: the device itself may be fine (wrong address).
KEEP_DEVICES_ONLINE = frozenset({"identity_mismatch"})
SEVERITY = {"ok": 0, "warn": 1, "error": 2}
KEY_MISMATCH_TEXT = (
    "Sleutel past niet, koppel opnieuw. De opgeslagen apparaatsleutel kan niet worden ontsleuteld met de "
    "huidige hoofdsleutel (bijvoorbeeld na herstel op een nieuwe server of een sleutelwissel)."
)


def fingerprint(integ: Integration) -> str:
    """Changes whenever the configuration or the stored credentials of an integration change."""
    h = hashlib.sha256()
    h.update(integ.provider.encode())
    h.update(json.dumps(integ.config or {}, sort_keys=True, default=str).encode())
    h.update(bytes(integ.credentials_enc or b""))
    return h.hexdigest()


async def patch_diagnostics(db, integ_id: uuid.UUID, patch: dict) -> None:
    """Merge top-level keys into ``Integration.diagnostics`` in SQL (no lost updates between loops)."""
    merged = func.coalesce(Integration.diagnostics, literal({}, JSONB)).op("||")(literal(patch, JSONB))
    await db.execute(update(Integration).where(Integration.id == integ_id).values(diagnostics=merged))


def is_db_unavailable(exc: BaseException) -> bool:
    """The database connection dropped or could not be made (restart, network, admin terminate).
    The pool replaces the connection (pool_pre_ping), so the loop simply tries again."""
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    if isinstance(exc, asyncpg.PostgresConnectionError | asyncpg.InterfaceError | asyncpg.CannotConnectNowError):
        return True
    if isinstance(exc, DBAPIError):
        if exc.connection_invalidated or isinstance(exc, OperationalError | InterfaceError):
            return True
        cause = exc.orig.__cause__ if exc.orig is not None else None
        return isinstance(cause, asyncpg.PostgresConnectionError | asyncpg.InterfaceError | OSError)
    return False


def is_expected_error(exc: BaseException) -> bool:
    """Network trouble, adapter errors and a dropped database connection: logged as one line,
    never with a traceback."""
    from shc_integrations.base import AdapterError

    return isinstance(exc, AdapterError | httpx.HTTPError | OSError | TimeoutError) or is_db_unavailable(exc)


def error_text(exc: BaseException) -> str:
    """Plain Dutch description of an adapter/network error (adapter errors are already Dutch)."""
    from shc_integrations.base import AdapterError

    if isinstance(exc, AdapterError):
        return str(exc) or "Fout in de koppeling"
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return "Niet bereikbaar: geen antwoord binnen de tijd"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Het apparaat gaf een foutmelding (HTTP {exc.response.status_code})"
    if isinstance(exc, httpx.TransportError | OSError):
        return "Niet bereikbaar: geen verbinding met het apparaat"
    return f"Onverwachte fout ({type(exc).__name__})"


def error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_error"
    if isinstance(exc, httpx.TransportError | OSError):
        return "device_offline"
    return "adapter_error"


@dataclass
class SourceStatus:
    """One source of integration status (poll, push stream, health check, loading)."""

    state: str = "ok"
    code: str | None = None
    detail: str = ""
    failures: int = 0
    failing_since: datetime | None = None


@dataclass
class IntegrationStatus:
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    reported: tuple[str, str | None, str] | None = None  # (state, code, detail) as written to the database
    since: datetime | None = None
    missed_polls: int = 0
    consecutive_poll_failures: int = 0
    last_ok_written: datetime | None = None
    logged_traceback: bool = False

    def effective(self) -> tuple[str, str | None, str]:
        worst: SourceStatus | None = None
        for src in self.sources.values():
            if worst is None or SEVERITY.get(src.state, 0) > SEVERITY.get(worst.state, 0):
                worst = src
        if worst is None or worst.state == "ok":
            ok = self.sources.get("poll") or self.sources.get("health") or SourceStatus(detail="Verbonden")
            return "ok", None, ok.detail or "Verbonden"
        return worst.state, worst.code, worst.detail


class Worker(IngestMixin, CommandsMixin, ClimateMixin, HealthMixin, BackupMixin, AggregatesMixin):
    alive_file: Path = ALIVE_FILE
    push_retry_s: float = 5.0  # first wait before restarting a failed push stream (grows per failure)

    def __init__(self, *, exit_fn: Callable[[int], object] | None = None) -> None:
        # Ends the process when a loop is stuck (os._exit: no cleanup that could hang as well).
        # Tests inject a recorder instead.
        self.exit_fn: Callable[[int], object] = exit_fn or os._exit
        self._watchdog_stop = threading.Event()
        self.settings = get_settings()
        self.box = SecretBox(self.settings.resolved_secret_key())
        self.adapters: dict[uuid.UUID, object] = {}
        self.integrations: dict[uuid.UUID, Integration] = {}
        self.fingerprints: dict[uuid.UUID, str] = {}
        self.integ_tasks: dict[uuid.UUID, list[asyncio.Task]] = {}
        self.load_failed: dict[uuid.UUID, str] = {}  # integration id -> fingerprint that failed to load
        self.status: dict[uuid.UUID, IntegrationStatus] = {}
        self.tasks: list[asyncio.Task] = []
        self.wake_commands = asyncio.Event()
        self.wake_reload = asyncio.Event()  # set by an "integration.changed" notification from the API
        self.started_at = datetime.now(UTC)
        # Liveness (contract C3): last completed iteration (monotonic) and interval per watched loop.
        self.loop_seen: dict[str, float] = {}
        self.loop_interval: dict[str, float] = {}
        self._stuck_logged: frozenset[str] = frozenset()
        # Maintenance (contract C8): cached, checked at most every MAINTENANCE_CHECK_S.
        self._maint = False
        self._maint_checked = -1e9

    # --- lifecycle -----------------------------------------------------------------

    def loop_specs(self) -> list[tuple[str, object, float, asyncio.Event | None]]:
        """(name, coroutine function, interval, wake event) of every watched loop (contract C2)."""
        specs: list[tuple[str, object, float, asyncio.Event | None]] = [
            ("heartbeat", self.heartbeat, HEARTBEAT_INTERVAL, None),
            ("commands", self.process_commands, 5, self.wake_commands),
            ("climate", self.climate_round, self.settings.climate_interval_s, None),
            ("health", self.health_round, 60, None),
            ("reload", self.load_integrations, RELOAD_INTERVAL, self.wake_reload),
            ("backup", self.backup_round, 600, None),
        ]
        # Energy loops live in worker/aggregates.py (energy stream); the worker starts without them.
        for name, attr, interval in (
            ("aggregate", "aggregate_round", 300),
            ("retention", "retention_round", 3600),
            ("live", "live_round", 5),
        ):
            fn = getattr(self, attr, None)
            if callable(fn):
                specs.append((name, fn, interval, None))
        return specs

    async def run(self) -> None:
        try:
            await self.load_integrations()
        except Exception:
            log.exception("Koppelingen laden bij het starten mislukt; de herlaadlus probeert het opnieuw")
        self.tasks = [
            asyncio.create_task(self._forever(name, fn, interval, wake))
            for name, fn, interval, wake in self.loop_specs()
        ]
        self.tasks.append(asyncio.create_task(self.listen()))
        self.start_watchdog()
        try:
            await asyncio.gather(*self.tasks)
        finally:
            self._watchdog_stop.set()

    async def in_maintenance(self) -> bool:
        """True while a restore runs (Setting "maintenance"). Cached; a failed read keeps the last value."""
        now = time.monotonic()
        if now - self._maint_checked < MAINTENANCE_CHECK_S:
            return self._maint
        self._maint_checked = now
        try:
            async with sessionmaker()() as db:
                value = bool(await get_setting(db, "maintenance", False))
        except Exception as exc:
            log.warning("Onderhoudsstand niet leesbaar (%s); vorige waarde blijft", exc)
            return self._maint
        if value != self._maint:
            log.info("Onderhoudsmodus %s", "aan: lussen gepauzeerd" if value else "uit: lussen lopen weer")
        self._maint = value
        return value

    async def _forever(
        self, name: str, fn, interval: float, wake: asyncio.Event | None = None, *, watch: bool = True
    ) -> None:
        if watch:
            self.loop_interval[name] = float(interval)
            self.loop_seen[name] = time.monotonic()  # first iteration gets the full grace period
        backoff = 1.0
        while True:
            try:
                if name == "heartbeat" or not await self.in_maintenance():
                    await fn()
                backoff = 1.0
                self.loop_seen[name] = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if is_expected_error(exc):  # e.g. database briefly unreachable: one line, no traceback
                    log.warning("Lus %s faalde: %s", name, exc or type(exc).__name__)
                else:
                    log.exception("Lus %s faalde", name)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            if wake is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=interval)
                wake.clear()
            else:
                await asyncio.sleep(interval)

    async def listen(self) -> None:
        dsn = self.settings.database_url.replace("+asyncpg", "")
        while True:
            try:
                conn = await asyncpg.connect(dsn)

                def on(_c, _p, _ch, payload: str) -> None:
                    if '"command.queued"' in payload:
                        self.wake_commands.set()
                    elif '"integration.changed"' in payload:
                        self.wake_reload.set()

                await conn.add_listener(events.CHANNEL, on)
                while not conn.is_closed():
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("LISTEN faalde: %s", exc)
                await asyncio.sleep(5)

    # --- heartbeat & liveness ---------------------------------------------------------

    def stuck_loops(self, now: float | None = None) -> list[str]:
        """Watched loops that did not complete an iteration within 5 x their interval + 60 s.
        Also called from the watchdog thread: it only reads (copies of) the two dicts."""
        now = time.monotonic() if now is None else now
        return sorted(
            name
            for name, interval in list(self.loop_interval.items())
            if now - self.loop_seen.get(name, now) > 5 * interval + 60
        )

    def exit_if_stuck(self, now: float | None = None) -> list[str]:
        """Log clearly and end the process (exit code 1) when a watched loop is stuck, so Docker's
        ``restart: unless-stopped`` starts a fresh worker. Returns the stuck loops (empty: nothing done)."""
        try:
            stuck = self.stuck_loops(now)
        except RuntimeError:  # a dict changed size while the watchdog thread copied it: next round
            return []
        if not stuck:
            return []
        now = time.monotonic() if now is None else now
        ages = ", ".join(f"{n} ({int(now - self.loop_seen.get(n, now))} s)" for n in stuck)
        log.critical(
            "Lus %s staat stil (langer dan 5 x de interval + 60 s). De worker stopt met code 1 zodat "
            "Docker hem opnieuw start.",
            ages,
        )
        for h in logging.getLogger().handlers:
            with contextlib.suppress(Exception):
                h.flush()
        with contextlib.suppress(Exception):
            sys.stderr.flush()
            sys.stdout.flush()
        self.exit_fn(1)
        return stuck

    def start_watchdog(self, check_s: float = WATCHDOG_CHECK_S) -> threading.Thread:
        """A thread, not a task: it also notices an event loop that is blocked as a whole."""

        def watch() -> None:
            while not self._watchdog_stop.wait(check_s):
                if self.exit_if_stuck():
                    return

        t = threading.Thread(target=watch, name="shc-worker-watchdog", daemon=True)
        t.start()
        return t

    def loops_report(self, now_wall: datetime | None = None) -> dict:
        now_wall = now_wall or datetime.now(UTC)
        now = time.monotonic()
        loops = {}
        for name, interval in sorted(self.loop_interval.items()):
            seen = self.loop_seen.get(name, now)
            loops[name] = {
                "last": (now_wall - timedelta(seconds=now - seen)).isoformat(),
                "interval": interval,
                "stuck": now - seen > 5 * interval + 60,
            }
        return {
            "at": now_wall.isoformat(),
            "started_at": self.started_at.isoformat(),
            "loops": loops,
            "integrations": len(self.adapters),
        }

    async def heartbeat(self) -> None:
        now = datetime.now(UTC)
        stuck = self.stuck_loops()
        if not await self.in_maintenance():
            async with sessionmaker()() as db:
                await set_setting(db, "worker_heartbeat", now.isoformat())
                await set_setting(db, "worker_loops", self.loops_report(now))
                # The API raises "worker_heartbeat:<home>" when the heartbeat goes stale; we are back.
                keys = (
                    await db.execute(
                        select(Alert.key).where(Alert.key.like("worker_heartbeat:%"), Alert.resolved_at.is_(None))
                    )
                ).scalars()
                for key in set(keys):
                    await resolve_alert(db, key)
                await db.commit()
        if stuck:
            if frozenset(stuck) != self._stuck_logged:
                log.error("Lus %s staat stil; de healthcheck van de worker faalt nu", ", ".join(stuck))
                self._stuck_logged = frozenset(stuck)
            return
        if self._stuck_logged:
            log.info("Alle lussen lopen weer")
            self._stuck_logged = frozenset()
        self.write_alive(now)

    def write_alive(self, now: datetime) -> None:
        try:
            tmp = self.alive_file.with_name(self.alive_file.name + ".tmp")
            tmp.write_text(now.isoformat())
            tmp.replace(self.alive_file)
        except OSError as exc:
            log.warning("Levensteken %s niet geschreven: %s", self.alive_file, exc)

    # --- adapters: loading, stopping, reloading ----------------------------------------

    async def load_integrations(self, home_ids: set[uuid.UUID] | None = None) -> None:
        """Bring the running adapters in line with the database (also the "reload" loop).
        ``home_ids`` limits it to some homes (tests, diagnostics)."""
        from shc_integrations.registry import ADAPTERS

        async with sessionmaker()() as db:
            q = select(Integration, Home.active_to).join(Home, Home.id == Integration.home_id)
            if home_ids:
                q = q.where(Integration.home_id.in_(home_ids))
            rows = (await db.execute(q)).all()
        wanted: dict[uuid.UUID, Integration] = {}
        for integ, archived_at in rows:
            if integ.enabled and archived_at is None and integ.provider in ADAPTERS:
                wanted[integ.id] = integ
        for iid in list(self.adapters):
            known = self.integrations.get(iid)
            if home_ids and known is not None and known.home_id not in home_ids:
                continue
            integ = wanted.get(iid)
            if integ is None:
                await self.stop_integration(iid, "uitgeschakeld, verwijderd of woning gearchiveerd", forget=True)
            elif self.fingerprints.get(iid) != fingerprint(integ):
                await self.stop_integration(iid, "instellingen gewijzigd; wordt opnieuw gestart")
            else:
                self.integrations[iid] = integ  # name/home changes
        for iid in list(self.load_failed):
            known = self.integrations.get(iid)
            if home_ids and known is not None and known.home_id not in home_ids:
                continue
            integ = wanted.get(iid)
            if integ is None:
                self.load_failed.pop(iid, None)
                self.status.pop(iid, None)
            elif self.load_failed[iid] != fingerprint(integ):
                self.load_failed.pop(iid, None)  # re-paired or key fixed: try again
        for iid, integ in wanted.items():
            if iid in self.adapters or iid in self.load_failed:
                continue
            await self.start_integration(integ, ADAPTERS[integ.provider])

    async def start_integration(self, integ: Integration, cls) -> bool:
        fp = fingerprint(integ)
        self.integrations[integ.id] = integ
        try:
            creds = self.box.decrypt(integ.credentials_enc) if integ.credentials_enc else {}
        except Exception:
            self.load_failed[integ.id] = fp
            log.error("Koppeling %s (%s): sleutel past niet; koppel opnieuw", integ.name, integ.provider)
            await self.report(integ.id, "load", "error", "key_mismatch", KEY_MISMATCH_TEXT)
            return False
        kwargs = {}
        if integ.provider == "homewizard":
            kwargs["relay_allowed"] = lambda _identity: False  # sockets are meters here; policy.py agrees
        if integ.provider == "zigbee" and self.settings.mqtt_password_file:
            try:
                creds.setdefault("password", self.settings.mqtt_password_file.read_text().strip())
            except OSError as exc:
                log.warning("MQTT-wachtwoordbestand niet leesbaar: %s", exc)
        cfg = dict(integ.config or {})
        if integ.provider == "zigbee":
            cfg.setdefault("host", self.settings.mqtt_host)
            cfg.setdefault("port", self.settings.mqtt_port)
        try:
            adapter = cls(cfg, creds, **kwargs)
        except Exception as exc:
            self.load_failed[integ.id] = fp
            log.error("Koppeling %s kon niet starten: %s", integ.name, exc)
            text = f"Koppeling kon niet starten: {error_text(exc)}"
            await self.report(integ.id, "load", "error", "load_failed", text)
            return False
        self.adapters[integ.id] = adapter
        self.fingerprints[integ.id] = fp
        st = self.status.get(integ.id)
        if st is not None and "load" in st.sources:
            st.sources.pop("load")
            await self._apply_status(integ.id)
        tasks = []
        interval = POLL_INTERVAL.get(integ.provider, 30)
        if interval:
            tasks.append(
                asyncio.create_task(
                    self._forever(f"poll:{integ.name}", lambda i=integ.id: self.poll(i), interval, watch=False)
                )
            )
        tasks.append(asyncio.create_task(self.subscribe(integ.id)))
        self.integ_tasks[integ.id] = tasks  # keeps a reference: asyncio holds tasks only weakly
        log.info("Koppeling geladen: %s (%s)", integ.name, integ.provider)
        return True

    async def stop_integration(self, integ_id: uuid.UUID, reason: str, *, forget: bool = False) -> None:
        """Cancel the poll/subscribe tasks of one integration and close its adapter."""
        tasks = self.integ_tasks.pop(integ_id, [])
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        adapter = self.adapters.pop(integ_id, None)
        if adapter is not None:
            with contextlib.suppress(Exception):
                await adapter.close()
        self.fingerprints.pop(integ_id, None)
        integ = self.integrations.get(integ_id)
        log.info("Koppeling %s gestopt: %s", integ.name if integ else integ_id, reason)
        if forget:
            self.integrations.pop(integ_id, None)
            self.status.pop(integ_id, None)
            async with sessionmaker()() as db:
                await self._resolve_integration_alerts(db, integ_id)
                await db.commit()
        else:
            self.status.pop(integ_id, None)

    async def poll(self, integ_id: uuid.UUID) -> None:
        adapter = self.adapters.get(integ_id)
        integ = self.integrations.get(integ_id)
        if adapter is None or integ is None:
            return
        st = self.status.setdefault(integ_id, IntegrationStatus())
        try:
            evs = await asyncio.wait_for(adapter.poll(), timeout=POLL_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            st.missed_polls += 1
            st.consecutive_poll_failures += 1
            if not is_expected_error(exc) and not st.logged_traceback:
                log.exception("Uitlezen van %s faalde onverwacht", integ.name)
                st.logged_traceback = True
            await self.report(integ_id, "poll", "error", error_code(exc), error_text(exc))
            return
        st.consecutive_poll_failures = 0
        st.logged_traceback = False
        await self.handle_events(integ, evs)
        await self.report(integ_id, "poll", "ok", None, "Verbonden")

    async def subscribe(self, integ_id: uuid.UUID) -> None:
        """Follow the adapter's push stream (Hue eventstream, MQTT). Events that arrive together are
        stored in one transaction. The adapter reconnects by itself; when the stream raises, it is
        restarted here with back-off and reported (debounced) as a push problem."""
        failures = 0
        while True:
            adapter = self.adapters.get(integ_id)
            integ = self.integrations.get(integ_id)
            if adapter is None or integ is None:
                return
            try:
                await self._pump(integ_id, adapter)
                return  # stream ended: the adapter has no push stream
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                if failures == 1:
                    if is_expected_error(exc):
                        log.warning("Live-verbinding %s verbroken: %s", integ.name, error_text(exc))
                    else:
                        log.exception("Live-verbinding %s faalde onverwacht", integ.name)
                await self.report(integ_id, "push", "error", error_code(exc), error_text(exc))
                await asyncio.sleep(min(60.0, self.push_retry_s * failures))

    async def _pump(self, integ_id: uuid.UUID, adapter) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        end = object()

        async def produce() -> None:
            try:
                async for ev in adapter.subscribe():
                    await queue.put(ev)
            finally:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(end)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is end:
                    break
                batch = [item]
                finished = False
                while len(batch) < 500:
                    try:
                        nxt = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if nxt is end:
                        finished = True
                        break
                    batch.append(nxt)
                integ = self.integrations.get(integ_id)
                if integ is not None and not await self.in_maintenance():
                    await self.handle_events(integ, batch)
                    st = self.status.get(integ_id)
                    if st is not None and st.sources.get("push") and st.sources["push"].state != "ok":
                        await self.report(integ_id, "push", "ok", None, "Verbonden")
                if finished:
                    break
        finally:
            if not producer.done():
                producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer  # re-raises the stream's error, if any

    # --- integration status (debounced) -------------------------------------------------

    async def report(
        self, integ_id: uuid.UUID, source: str, state: str, code: str | None = None, detail: str = ""
    ) -> None:
        """Record what one source says about an integration and write the combined state on change."""
        now = datetime.now(UTC)
        if state not in SEVERITY:
            state = "warn"
        st = self.status.setdefault(integ_id, IntegrationStatus())
        src = st.sources.setdefault(source, SourceStatus())
        if state == "error":
            src.failures += 1
            src.failing_since = src.failing_since or now
            if (
                code not in IMMEDIATE_CODES
                and src.failures < FAIL_THRESHOLD
                and now - src.failing_since < FAIL_WINDOW
                and src.state != "error"
            ):
                return  # transient: wait for more failures before calling it an error
        else:
            src.failures, src.failing_since = 0, None
        src.state, src.code, src.detail = state, code, (detail or "")[:500]
        await self._apply_status(integ_id, now)

    async def mark_integration(self, integ_id: uuid.UUID, state: str, detail: str) -> None:
        """Backwards compatible shortcut: a poll result."""
        await self.report(integ_id, "poll", state, None, detail)

    async def _apply_status(self, integ_id: uuid.UUID, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        st = self.status.setdefault(integ_id, IntegrationStatus())
        new = st.effective()
        if new == st.reported:
            if new[0] == "ok" and (st.last_ok_written is None or now - st.last_ok_written > LAST_OK_WRITE_EVERY):
                async with sessionmaker()() as db:
                    await db.execute(update(Integration).where(Integration.id == integ_id).values(last_ok_at=now))
                    await db.commit()
                st.last_ok_written = now
            return
        old = st.reported
        st.reported = new
        st.since = now
        state, code, detail = new
        async with sessionmaker()() as db:
            integ = await db.get(Integration, integ_id)
            if integ is None:
                return
            integ.state, integ.detail = state, detail[:500]
            if state == "ok":
                integ.last_ok_at = now
                st.last_ok_written = now
            await patch_diagnostics(
                db,
                integ_id,
                {
                    "status": {
                        "state": state,
                        "code": code,
                        "detail": detail,
                        "since": now.isoformat(),
                        "missed_polls": st.missed_polls,
                        "consecutive_poll_failures": st.consecutive_poll_failures,
                    }
                },
            )
            await self._integration_alerts(db, integ, state, code, detail)
            if state == "error" and code not in KEEP_DEVICES_ONLINE:
                await db.execute(update(Device).where(Device.integration_id == integ_id).values(online=False))
            await db.flush()
            from ..views.status import status_checks

            await events.publish(db, "status.updated", integ.home_id, await status_checks(db, integ.home_id))
            await db.commit()
        was = old[0] if old else "onbekend"
        line = "Koppeling %s: %s -> %s%s"
        args = (integ.name, was, state, f" ({code}: {detail})" if state != "ok" else "")
        (log.info if state == "ok" else log.warning)(line, *args)

    async def _resolve_integration_alerts(self, db, integ_id: uuid.UUID, keep: str | None = None) -> None:
        keys = (
            await db.execute(
                select(Alert.key).where(Alert.key.like(f"integration:{integ_id}%"), Alert.resolved_at.is_(None))
            )
        ).scalars()
        for key in set(keys):
            if key != keep:
                await resolve_alert(db, key)

    async def _integration_alerts(self, db, integ: Integration, state: str, code: str | None, detail: str) -> None:
        if state != "error":
            await self._resolve_integration_alerts(db, integ.id)
            return
        key = f"integration:{integ.id}:{code or 'error'}"
        await self._resolve_integration_alerts(db, integ.id, keep=key)
        title, extra = alert_text(integ, code)
        await raise_alert(db, integ.home_id, key, "error", title, f"{detail} {extra}".strip(), integ.provider)

    async def mark_devices_offline(self, integ: Integration) -> None:
        async with sessionmaker()() as db:
            await db.execute(update(Device).where(Device.integration_id == integ.id).values(online=False))
            await db.commit()

    def _adapter_for(self, device: Device):
        return self.adapters.get(device.integration_id) if device.integration_id else None


def alert_text(integ: Integration, code: str | None) -> tuple[str, str]:
    """(title, extra explanation) for an integration error alert, in plain Dutch."""
    name = integ.name
    if code == "identity_mismatch":
        return (
            f"{name}: apparaat op ander adres",
            "Op het ingestelde adres antwoordt een ander apparaat; er worden geen metingen opgeslagen. "
            "Wijzig het adres via Apparaten of koppel opnieuw, en geef het apparaat een vast IP-adres "
            "(DHCP-reservering in je router).",
        )
    if code == "certificate_mismatch":
        return (
            f"{name}: certificaat klopt niet",
            "Het apparaat op dit adres heeft een ander certificaat dan bij het koppelen. Er wordt niets "
            "verstuurd. Controleer het adres of koppel opnieuw.",
        )
    if code == "key_mismatch":
        return f"{name}: sleutel past niet, koppel opnieuw", ""
    if code == "pairing_required":
        return f"{name}: opnieuw koppelen nodig", "Het apparaat weigert de opgeslagen sleutel."
    if code == "mqtt_disconnected":
        return "Zigbee: geen verbinding met MQTT", "Controleer of de mosquitto-container draait."
    if code == "bridge_offline":
        return "Zigbee-dongle offline", "Controleer of de USB-dongle is aangesloten en Zigbee2MQTT draait."
    if code == "load_failed":
        return f"{name}: koppeling start niet", ""
    return f"{name} niet bereikbaar", ""


async def run() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await Worker().run()
