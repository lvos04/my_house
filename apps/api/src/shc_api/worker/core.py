"""Worker lifecycle: loop runner, LISTEN, heartbeat and adapter loading/polling/subscribing.

The concern-specific loops live in the mixins this class is assembled from.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime

import asyncpg
from sqlalchemy import select, text, update

from .. import events
from ..config import get_settings
from ..db import sessionmaker
from ..models import Device, Integration
from ..security import SecretBox
from ..store import raise_alert, resolve_alert, set_setting
from .aggregates import AggregatesMixin
from .backup_job import BackupMixin
from .climate_loop import ClimateMixin
from .commands import CommandsMixin
from .health import HealthMixin
from .ingest import IngestMixin

log = logging.getLogger("shc.worker")

POLL_INTERVAL = {"homewizard": 10, "gree": 30, "hue": 120, "zigbee": 0}


class Worker(IngestMixin, CommandsMixin, ClimateMixin, HealthMixin, BackupMixin, AggregatesMixin):
    def __init__(self) -> None:
        self.settings = get_settings()
        self.box = SecretBox(self.settings.resolved_secret_key())
        self.adapters: dict[uuid.UUID, object] = {}
        self.integrations: dict[uuid.UUID, Integration] = {}
        self.tasks: list[asyncio.Task] = []
        self.wake_commands = asyncio.Event()
        self.started_at = datetime.now(UTC)

    # --- lifecycle -----------------------------------------------------------------

    async def run(self) -> None:
        await self.load_integrations()
        self.tasks = [
            asyncio.create_task(self._forever("heartbeat", self.heartbeat, 30)),
            asyncio.create_task(self._forever("commands", self.process_commands, 5, self.wake_commands)),
            asyncio.create_task(self._forever("climate", self.climate_round, self.settings.climate_interval_s)),
            asyncio.create_task(self._forever("health", self.health_round, 60)),
            asyncio.create_task(self._forever("reload", self.load_integrations, 120)),
            asyncio.create_task(self._forever("backup", self.backup_round, 600)),
            asyncio.create_task(self.listen()),
        ]
        await asyncio.gather(*self.tasks)

    async def _forever(self, name, fn, interval: float, wake: asyncio.Event | None = None) -> None:
        backoff = 1.0
        while True:
            try:
                await fn()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
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

                await conn.add_listener(events.CHANNEL, on)
                while not conn.is_closed():
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("LISTEN faalde: %s", exc)
                await asyncio.sleep(5)

    async def heartbeat(self) -> None:
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", datetime.now(UTC).isoformat())
            db_now = (await db.execute(text("SELECT now()"))).scalar_one()
            drift = abs((db_now - datetime.now(UTC)).total_seconds())
            await set_setting(
                db,
                "clock_check",
                {"state": "ok" if drift < 30 else "warn", "detail": f"Verschil met database {drift:.1f} s"},
            )
            await db.commit()

    # --- adapters -------------------------------------------------------------------

    async def load_integrations(self) -> None:
        from shc_integrations.registry import ADAPTERS

        async with sessionmaker()() as db:
            rows = (await db.execute(select(Integration).where(Integration.enabled.is_(True)))).scalars().all()
        for integ in rows:
            if integ.id in self.adapters or integ.provider not in ADAPTERS:
                continue
            creds = self.box.decrypt(integ.credentials_enc) if integ.credentials_enc else {}
            kwargs = {}
            if integ.provider == "homewizard":
                kwargs["relay_allowed"] = lambda _identity: False  # sockets are meters here; policy.py agrees
            if integ.provider == "zigbee" and self.settings.mqtt_password_file:
                creds.setdefault("password", self.settings.mqtt_password_file.read_text().strip())
            cfg = dict(integ.config)
            if integ.provider == "zigbee":
                cfg.setdefault("host", self.settings.mqtt_host)
                cfg.setdefault("port", self.settings.mqtt_port)
            adapter = ADAPTERS[integ.provider](cfg, creds, **kwargs)
            self.adapters[integ.id] = adapter
            self.integrations[integ.id] = integ
            interval = POLL_INTERVAL.get(integ.provider, 30)
            if interval:
                self.tasks.append(
                    asyncio.create_task(self._forever(f"poll:{integ.name}", lambda i=integ.id: self.poll(i), interval))
                )
            self.tasks.append(asyncio.create_task(self.subscribe(integ.id)))
            log.info("Integratie geladen: %s (%s)", integ.name, integ.provider)

    async def poll(self, integ_id: uuid.UUID) -> None:
        adapter = self.adapters[integ_id]
        integ = self.integrations[integ_id]
        try:
            evs = await adapter.poll()
        except Exception as exc:
            await self.mark_integration(integ_id, "error", f"Niet bereikbaar: {exc}")
            await self.mark_devices_offline(integ)
            raise
        await self.handle_events(integ, evs)
        await self.mark_integration(integ_id, "ok", "Verbonden")

    async def subscribe(self, integ_id: uuid.UUID) -> None:
        adapter = self.adapters[integ_id]
        integ = self.integrations[integ_id]
        while True:
            try:
                async for ev in adapter.subscribe():
                    await self.handle_events(integ, [ev])
                return  # adapter has no push stream
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Subscribe %s faalde: %s", integ.name, exc)
                await self.mark_integration(integ_id, "error", str(exc))
                await asyncio.sleep(10)

    async def mark_integration(self, integ_id: uuid.UUID, state: str, detail: str) -> None:
        async with sessionmaker()() as db:
            vals = {"state": state, "detail": detail[:500]}
            if state == "ok":
                vals["last_ok_at"] = datetime.now(UTC)
            await db.execute(update(Integration).where(Integration.id == integ_id).values(**vals))
            integ = self.integrations[integ_id]
            key = f"integration:{integ_id}"
            if state == "error":
                await raise_alert(
                    db, integ.home_id, key, "error", f"{integ.name} niet bereikbaar", detail, integ.provider
                )
            else:
                await resolve_alert(db, key)
            await db.commit()

    async def mark_devices_offline(self, integ: Integration) -> None:
        async with sessionmaker()() as db:
            await db.execute(update(Device).where(Device.integration_id == integ.id).values(online=False))
            await db.commit()

    def _adapter_for(self, device: Device):
        return self.adapters.get(device.integration_id) if device.integration_id else None


async def run() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await Worker().run()
