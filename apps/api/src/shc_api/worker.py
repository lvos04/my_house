"""Background worker: the only process that talks to devices.

Loops: adapter polling + push subscriptions, command queue, climate engine, health/alerts,
heartbeat and scheduled backups. Every loop is crash-only: an exception is logged, the
loop sleeps with backoff and continues. At startup no device commands are sent until the
unit state has been read back (no surprise switching after a power cut).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from sqlalchemy import select, text, update

from . import climate as cl
from . import events, views
from .config import get_settings
from .db import sessionmaker
from .models import (
    ButtonMapping,
    ClimateZone,
    CommandRecord,
    Device,
    Home,
    Integration,
    ZoneLog,
)
from .policy import DeviceFacts, PolicyDenied, check_command
from .security import SecretBox
from .store import get_setting, ingest_reading, raise_alert, resolve_alert, roles_of, set_setting

log = logging.getLogger("shc.worker")

POLL_INTERVAL = {"homewizard": 10, "gree": 30, "hue": 120, "zigbee": 0}
METRIC_MAP = {"unit_temperature": "temperature"}  # Gree internal sensor -> unit "temperature"
BATTERY_LOW = 15


class Worker:
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

    async def _device(self, db, integ: Integration, identity: str, kind: str, name: str) -> Device:
        d = (
            await db.execute(
                select(Device).where(
                    Device.home_id == integ.home_id,
                    Device.provider == integ.provider,
                    Device.stable_identity == identity,
                )
            )
        ).scalar_one_or_none()
        if d is None:
            d = Device(
                home_id=integ.home_id,
                integration_id=integ.id,
                provider=integ.provider,
                kind=kind,
                name=name,
                stable_identity=identity,
                compatibility="untested",
            )
            db.add(d)
            await db.flush()
        return d

    def _hue_name(self, integ: Integration, rid: str | None) -> str | None:
        res = getattr(self.adapters.get(integ.id), "_resources", {}) or {}
        r = res.get(rid) if rid else None
        return ((r or {}).get("metadata") or {}).get("name") if r else None

    async def handle_events(self, integ: Integration, evs: list) -> None:
        from shc_integrations.base import ButtonEvent, Reading, StateUpdate

        if not evs:
            return
        async with sessionmaker()() as db:
            home = await db.get(Home, integ.home_id)
            touched_units: set[uuid.UUID] = set()
            for ev in evs:
                if isinstance(ev, Reading):
                    kind = {"zigbee": "zigbee_sensor", "gree": "ac_unit"}.get(integ.provider, "meter")
                    d = await self._device(db, integ, ev.device_identity, kind, f"Sensor {ev.device_identity[-6:]}")
                    metric = METRIC_MAP.get(ev.metric, ev.metric)
                    await ingest_reading(
                        db,
                        d.id,
                        metric,
                        ev.value,
                        ev.unit,
                        ev.observed_at,
                        str(ev.quality),
                        ev.event_id,
                        None,
                    )
                    d.last_seen, d.online = ev.observed_at, True
                    if metric == "battery" and ev.value is not None:
                        key = f"battery:{d.id}"
                        if ev.value < BATTERY_LOW:
                            await raise_alert(
                                db,
                                d.home_id,
                                key,
                                "warn",
                                f"Batterij {d.name} bijna leeg",
                                f"{ev.value:.0f}% resterend",
                                "zigbee",
                            )
                        else:
                            await resolve_alert(db, key)
                elif isinstance(ev, StateUpdate):
                    st = ev.state
                    if integ.provider == "gree":
                        d = await self._device(db, integ, ev.device_identity, "ac_unit", integ.name)
                        mode = "off" if st.get("power") is False else st.get("mode")
                        d.state = {**(d.state or {}), **st, "mode": mode}
                        if st.get("target_c") is not None:
                            await ingest_reading(db, d.id, "target_c", st["target_c"], "°C", ev.observed_at)
                        touched_units.add(d.id)
                    elif integ.provider == "hue":
                        if st.get("type") == "grouped_light":
                            name = self._hue_name(integ, st.get("owner")) or "Lichtgroep"
                            d = await self._device(db, integ, ev.device_identity, "light_group", name)
                            if d.name == "Lichtgroep" and name != "Lichtgroep":
                                d.name = name
                        elif st.get("type") == "light":
                            d = await self._device(db, integ, ev.device_identity, "light", st.get("name") or "Lamp")
                        else:
                            d = await self._device(db, integ, ev.device_identity, "hue_device", "Hue-apparaat")
                        d.state = {**(d.state or {}), **st}
                        if d.kind == "light_group":
                            await events.publish(
                                db,
                                "lights.updated",
                                d.home_id,
                                {
                                    "id": d.id,
                                    "name": d.name,
                                    "on": bool(d.state.get("on")),
                                    "brightness": d.state.get("brightness"),
                                    "scene": d.state.get("scene"),
                                    "reachable": ev.online,
                                    "lights_on": d.state.get("lights_on", 0),
                                    "lights_total": d.state.get("lights_total", 0),
                                },
                            )
                    else:
                        d = await self._device(db, integ, ev.device_identity, "device", ev.device_identity)
                        d.state = {**(d.state or {}), **st}
                    d.online, d.last_seen = ev.online, ev.observed_at
                elif isinstance(ev, ButtonEvent):
                    await self.on_button(db, integ, ev)
            if integ.provider == "hue":
                await self._sync_hue_scenes(db, integ)
            await db.flush()
            if home:
                for z in (
                    await db.execute(select(ClimateZone).where(ClimateZone.unit_device_id.in_(touched_units)))
                ).scalars():
                    await events.publish(db, "zone.updated", home.id, await views.zone_view(db, z, home))
            await db.commit()

    async def _sync_hue_scenes(self, db, integ: Integration) -> None:
        res = getattr(self.adapters.get(integ.id), "_resources", {}) or {}
        group_by_owner = {}
        for r in res.values():
            if r.get("type") == "grouped_light":
                group_by_owner[(r.get("owner") or {}).get("rid")] = r.get("id")
        for r in res.values():
            if r.get("type") != "scene":
                continue
            d = await self._device(db, integ, r["id"], "scene", ((r.get("metadata") or {}).get("name")) or "Scène")
            grp_rid = group_by_owner.get((r.get("group") or {}).get("rid"))
            if grp_rid:
                g = (
                    await db.execute(
                        select(Device.id).where(Device.stable_identity == grp_rid, Device.home_id == integ.home_id)
                    )
                ).scalar_one_or_none()
                d.state = {"group_device_id": str(g) if g else None}

    async def on_button(self, db, integ: Integration, ev) -> None:
        dev = await self._device(db, integ, ev.device_identity, "button", "Hue-knop")
        maps = (
            (
                await db.execute(
                    select(ButtonMapping).where(
                        ButtonMapping.device_id == dev.id,
                        ButtonMapping.button == ev.button,
                        ButtonMapping.event == ev.event,
                    )
                )
            )
            .scalars()
            .all()
        )
        for m in maps:
            if m.mode == "combined" and not m.combined_confirmed:
                continue
            for step in m.action.get("steps", [m.action]):
                target = await db.get(Device, uuid.UUID(step["device_id"])) if step.get("device_id") else None
                if target is None:
                    continue
                db.add(
                    CommandRecord(
                        home_id=dev.home_id,
                        device_id=target.id,
                        action=step["action"],
                        params=step.get("params", {}),
                        source="button",
                        actor=f"knop {dev.name}",
                    )
                )
        self.wake_commands.set()

    # --- commands ---------------------------------------------------------------------

    def _adapter_for(self, device: Device):
        return self.adapters.get(device.integration_id) if device.integration_id else None

    async def process_commands(self) -> None:
        from shc_integrations.base import Command

        while True:
            async with sessionmaker()() as db:
                cmd = (
                    await db.execute(
                        select(CommandRecord)
                        .where(CommandRecord.status == "queued")
                        .order_by(CommandRecord.created_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if cmd is None:
                    return
                dev = await db.get(Device, cmd.device_id)
                cmd.attempts += 1
                cmd.updated_at = datetime.now(UTC)
                try:
                    roles, protected = await roles_of(db, dev.id)
                    check_command(
                        DeviceFacts(dev.kind, roles, protected, bool(await get_setting(db, "safe_mode", False))),
                        cmd.action,
                        cmd.params,
                    )
                    if datetime.now(UTC) - cmd.created_at > timedelta(minutes=2):
                        raise TimeoutError("Commando te oud; niet meer uitgevoerd")
                    adapter = self._adapter_for(dev)
                    if adapter is None:
                        raise RuntimeError("Geen actieve integratie voor dit apparaat")
                    action, params = cmd.action, dict(cmd.params)
                    if dev.provider == "gree" and action == "set_hvac":
                        if params.get("mode") == "off":
                            params = {"power": False}
                        else:
                            params = {"power": True, **params}
                    res = await adapter.execute(Command(dev.stable_identity, action, params))
                    cmd.status = "confirmed" if res.confirmed else ("sent" if res.ok else "failed")
                    cmd.detail = res.detail
                    if res.state and dev.provider == "gree":
                        mode = "off" if res.state.get("power") is False else res.state.get("mode")
                        dev.state = {
                            **(dev.state or {}),
                            **{k: v for k, v in res.state.items() if k != "raw"},
                            "mode": mode,
                        }
                        dev.last_seen, dev.online = datetime.now(UTC), True
                except PolicyDenied as e:
                    cmd.status, cmd.detail = "denied", e.message
                except Exception as e:
                    cmd.status, cmd.detail = "failed", str(e)[:500]
                if cmd.zone_id:
                    z = await db.get(ClimateZone, cmd.zone_id)
                    home = await db.get(Home, cmd.home_id)
                    if cmd.status in ("failed", "denied"):
                        db.add(ZoneLog(zone_id=z.id, kind="error", message=f"Commando niet uitgevoerd: {cmd.detail}"))
                    elif cmd.status == "sent":
                        db.add(ZoneLog(zone_id=z.id, kind="warn", message=f"Verzonden, niet bevestigd: {cmd.detail}"))
                    await events.publish(db, "zone.updated", home.id, await views.zone_view(db, z, home))
                await events.publish(
                    db,
                    "command.updated",
                    cmd.home_id,
                    {"id": cmd.id, "status": cmd.status, "detail": cmd.detail},
                )
                await db.commit()

    # --- climate ---------------------------------------------------------------------

    async def climate_round(self) -> None:
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            zones = (
                (await db.execute(select(ClimateZone).where(ClimateZone.unit_device_id.is_not(None)))).scalars().all()
            )
            for z in zones:
                home = await db.get(Home, z.home_id)
                if home is None or home.active_to is not None:
                    continue
                unit = await db.get(Device, z.unit_device_id)
                # Startup safety: wait until the unit state was read after this worker started.
                if unit is None or unit.last_seen is None or unit.last_seen < self.started_at:
                    continue
                sensor = None
                if z.sensor_device_id:
                    from .store import latest_reading

                    r = await latest_reading(db, z.sensor_device_id, "temperature")
                    sensor = cl.SensorValue(r.value if r else None, r.observed_at if r else None)
                zi = cl.ZoneInput(
                    now=now,
                    tz=ZoneInfo(home.timezone),
                    policy=cl.policy_with_defaults(z.policy),
                    caps=views.caps_of(unit),
                    control=z.control,
                    desired_mode=z.desired_mode,
                    desired_target_c=z.desired_target_c,
                    override_until=z.override_until,
                    sensor=sensor,
                    unit=views.unit_state(unit),
                    schedule=await views.schedule_entries(db, z.id),
                    last_command_at=z.last_command_at,
                    last_commanded=z.last_commanded or {},
                    start_times=[datetime.fromisoformat(t) for t in (z.start_times or [])],
                    other_zone_modes=await views.other_zone_modes(db, z),
                    adaptive_offset_c=await self._adaptive_offset(db, z, sensor, home),
                )
                d = cl.decide(zi)
                changed = d.control != z.control
                z.control = d.control
                if d.override_until is not None or d.control != "manual":
                    z.override_until = d.override_until
                if d.override_source:
                    z.override_source = d.override_source
                for line in d.log:
                    db.add(ZoneLog(zone_id=z.id, kind="engine", message=line))
                if d.send:
                    try:
                        check_command(
                            DeviceFacts(
                                unit.kind,
                                *(await roles_of(db, unit.id)),
                                bool(await get_setting(db, "safe_mode", False)),
                            ),
                            "set_hvac",
                            d.send,
                        )
                    except PolicyDenied as e:
                        db.add(ZoneLog(zone_id=z.id, kind="warn", message=e.message))
                        await db.commit()
                        continue
                    db.add(
                        CommandRecord(
                            home_id=home.id,
                            device_id=unit.id,
                            zone_id=z.id,
                            action="set_hvac",
                            params=d.send,
                            source="engine",
                            actor="klimaatregelaar",
                        )
                    )
                    if zi.unit.mode in (None, "off") and d.send.get("mode") not in (None, "off"):
                        z.start_times = [t.isoformat() for t in zi.start_times if now - t < timedelta(hours=1)] + [
                            now.isoformat()
                        ]
                    z.last_command_at = now
                    z.last_commanded = {**(z.last_commanded or {}), **d.send}
                    db.add(ZoneLog(zone_id=z.id, kind="engine", message=self._describe(d)))
                    self.wake_commands.set()
                if d.send or changed or d.log:
                    await events.publish(db, "zone.updated", home.id, await views.zone_view(db, z, home))
                await db.commit()

    async def _adaptive_offset(self, db, z: ClimateZone, sensor, home: Home) -> float:
        """Bounded advice from the active model; 0 unless the zone is opted in."""
        if not z.adaptive_enabled or sensor is None or sensor.value is None:
            return 0.0
        from .adaptive import advised_offset
        from .models import AdaptiveModelVersion

        m = (
            await db.execute(
                select(AdaptiveModelVersion).where(
                    AdaptiveModelVersion.zone_id == z.id, AdaptiveModelVersion.active.is_(True)
                )
            )
        ).scalar_one_or_none()
        entry = cl.scheduled_entry(await views.schedule_entries(db, z.id), datetime.now(UTC), ZoneInfo(home.timezone))
        if m is None or entry is None:
            return 0.0
        return advised_offset(m.params, entry.target_c, sensor.value, cl.policy_with_defaults(z.policy)["band_c"])

    @staticmethod
    def _describe(d: cl.Decision) -> str:
        s = d.send or {}
        if s.get("mode") == "off":
            return "Unit uit volgens schema"
        why = {"schedule": "schema", "fallback_schedule": "schema (sensor ongeldig, geen bijsturing)"}.get(
            d.reason, d.reason
        )
        return f"Setpoint {s.get('target_c')}° modus {s.get('mode')} · {why}"

    # --- health ----------------------------------------------------------------------

    async def health_round(self) -> None:
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            zones = (
                (await db.execute(select(ClimateZone).where(ClimateZone.sensor_device_id.is_not(None)))).scalars().all()
            )
            for z in zones:
                pol = cl.policy_with_defaults(z.policy)
                dev = await db.get(Device, z.sensor_device_id)
                key = f"stale:{dev.id}"
                if dev.last_seen is None or (now - dev.last_seen).total_seconds() > pol["stale_after_s"]:
                    mins = int((now - dev.last_seen).total_seconds() // 60) if dev.last_seen else None
                    await raise_alert(
                        db,
                        z.home_id,
                        key,
                        "warn",
                        f"Sensor {z.name} verouderd",
                        f"Laatste bericht {mins} min geleden" if mins is not None else "Nog geen bericht ontvangen",
                        "zigbee",
                    )
                else:
                    await resolve_alert(db, key)
            await db.commit()

    async def backup_round(self) -> None:
        s = self.settings
        pf = s.data_dir / "backup_passphrase"
        if not pf.exists():
            return
        async with sessionmaker()() as db:
            last = await get_setting(db, "last_auto_backup")
            if last and datetime.now(UTC) - datetime.fromisoformat(last) < timedelta(hours=23):
                return
            local_hour = datetime.now(ZoneInfo("Europe/Amsterdam")).hour
            if last and local_hour != 3:
                return
            from .routers.system import make_backup

            rec = await make_backup(db, pf.read_text().strip(), include_secrets=True)
            await set_setting(db, "last_auto_backup", datetime.now(UTC).isoformat())
            await db.commit()
            log.info("Automatische back-up: %s", rec.filename)
            keep = int(json.loads(json.dumps(await get_setting(db, "backup_keep", 14))))
            files = sorted(s.backup_dir.glob("shc-*.shcbak"))
            for old in files[:-keep]:
                old.unlink(missing_ok=True)


async def run() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await Worker().run()
