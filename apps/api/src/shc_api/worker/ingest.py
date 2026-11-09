"""Adapter events -> devices, readings, states, alerts and button actions.

Per batch of events (one poll, or what the push stream delivered together), in one transaction:

1. ``DeviceInfo`` first: creates devices with the right kind and name straight away (Zigbee
   friendly_name, Hue buttons with their buttons/events, scenes, groups), upgrades a generic kind
   ("device", "zigbee_device", "hue_device") to the specific one, follows renames made in the
   vendor app unless the user renamed the device here, and archives devices the hub reports as gone
   (verbeterlijst 8, 25, small item "Hue-scènes").
2. Readings and state updates: calibration offsets (raw value kept), Gree capabilities (C1), the
   Zigbee coordinator status on the existing ``zigbee_bridge`` device, HomeWizard socket relay /
   switch-lock alerts (verbeterlijst 15), Hue groups with counts/scene/reachability (27).
3. Button events: dropped when older than 10 s, de-duplicated on ``event_id`` (processed_events),
   then the mappings run: light steps via ``queue_command``, climate steps via
   ``commanding.request_zone_command`` (same checks as the API). The command queue is woken after
   the commit (verbeterlijst 25).

All device lookups of a batch are one query.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from .. import events, views
from ..commanding import CommandRejected, queue_command, request_zone_command, resume_zone
from ..db import sessionmaker
from ..models import ButtonMapping, ClimateZone, Device, Home, Integration, ProcessedEvent, ZoneLog
from ..policy import DEFAULT_PROTECTED_ROLES, PolicyDenied
from ..store import ingest_reading, raise_alert, resolve_alert, roles_of
from ..views.lights import group_view_for, light_command, mapping_steps

log = logging.getLogger("shc.worker")

METRIC_MAP = {"unit_temperature": "temperature"}  # Gree internal sensor -> unit "temperature"
BATTERY_LOW = 15
BUTTON_MAX_AGE = timedelta(seconds=10)
SENSOR_METRICS = frozenset({"temperature", "humidity"})
# Calibration (PATCH /devices/{id}/calibration): metric -> (key in Device.calibration, lower, upper bound).
CALIBRATION = {"temperature": ("temperature_offset", None, None), "humidity": ("humidity_offset", 0.0, 100.0)}
COORDINATOR_IDENTITY = "zigbee2mqtt-bridge"
COORDINATOR_NAME = "Zigbee-coördinator"
# Names the worker gives before it knows better; replaced by the adapter's name when it arrives.
DEFAULT_NAMES = frozenset({"Hue-knop", "Lichtgroep", "Hue-apparaat", "Lamp", "Scène", "Zigbee-apparaat"})
GREE_BASE_CAPS = ("power", "hvac_mode", "setpoint")


def default_name(kind: str, identity: str) -> str:
    return {
        "zigbee_sensor": f"Sensor {identity[-6:]}",
        "zigbee_device": f"Zigbee-apparaat {identity[-4:]}",
        "zigbee_bridge": COORDINATOR_NAME,
        "button": "Hue-knop",
        "light_group": "Lichtgroep",
        "light": "Lamp",
        "scene": "Scène",
        "hue_device": "Hue-apparaat",
    }.get(kind, f"Apparaat {identity[-6:]}")


def user_archived(d: Device) -> bool:
    return d.lifecycle == "archived" and (d.diagnostics or {}).get("archived_by") == "user"


def calibrate(d: Device, metric: str, value: float | None) -> tuple[float | None, dict | None]:
    """Apply the device's offset for temperature/humidity; returns (value to store, raw info or None)."""
    spec = CALIBRATION.get(metric)
    if spec is None or value is None:
        return value, None
    key, lo, hi = spec
    try:
        offset = float((d.calibration or {}).get(key) or 0.0)
    except (TypeError, ValueError):
        return value, None
    if not offset:
        return value, None
    new = value + offset
    if lo is not None:
        new = max(lo, new)
    if hi is not None:
        new = min(hi, new)
    return round(new, 2), {"raw_value": value, "offset": offset}


class _Batch:
    """Devices of one batch by identity, plus what changed (for realtime events)."""

    def __init__(self, integ: Integration, home: Home):
        self.integ = integ
        self.home = home
        self.devices: dict[str, Device] = {}
        self.units: set[uuid.UUID] = set()
        self.groups: dict[uuid.UUID, Device] = {}
        self.queued = False


class IngestMixin:
    # Provided by core.Worker.
    adapters: dict[uuid.UUID, object]
    wake_commands: asyncio.Event

    # --- device lookup -----------------------------------------------------------------------

    async def _load_devices(self, db, b: _Batch, identities: set[str]) -> None:
        missing = [i for i in identities if i and i not in b.devices]
        if not missing:
            return
        rows = (
            await db.execute(
                select(Device).where(
                    Device.home_id == b.integ.home_id,
                    Device.provider == b.integ.provider,
                    Device.stable_identity.in_(missing),
                )
            )
        ).scalars()
        for d in rows:
            b.devices[d.stable_identity] = d

    def _adapter_info(self, integ: Integration, identity: str):
        adapter = self.adapters.get(integ.id)
        fn = getattr(adapter, "device_info", None)
        if not callable(fn):
            return None
        try:
            return fn(identity)
        except Exception:  # an adapter bug must never stop ingestion
            return None

    async def _device(
        self, db, b: _Batch, identity: str, kind: str, name: str | None = None, *, reactivate: bool = True
    ) -> Device:
        """The device for an identity; created when new, with the adapter's kind/name when it knows them."""
        from shc_integrations.base import GENERIC_KINDS

        await self._load_devices(db, b, {identity})
        d = b.devices.get(identity)
        if d is None:
            info = self._adapter_info(b.integ, identity)
            if info is not None and (info.kind not in GENERIC_KINDS or kind in GENERIC_KINDS):
                kind = info.kind
            name = (info.name if info is not None and info.name else None) or name or default_name(kind, identity)
            d = Device(
                home_id=b.integ.home_id,
                integration_id=b.integ.id,
                provider=b.integ.provider,
                kind=kind,
                name=name[:100],
                stable_identity=identity,
                compatibility="untested",
                lifecycle="active",
                state={},
                diagnostics={},
                calibration={},
                capabilities=[],
            )
            if info is not None:
                d.model = (info.model or "")[:100] or None
                d.firmware = (info.firmware or "")[:64] or None
            db.add(d)
            await db.flush()
            b.devices[identity] = d
        elif d.integration_id != b.integ.id and not user_archived(d):
            d.integration_id = b.integ.id  # served by the current integration (re-pairing, verbeterlijst 24)
        if reactivate and d.lifecycle == "archived" and (d.diagnostics or {}).get("archived_by") == "adapter":
            d.lifecycle = "active"  # it is back on the hub
            d.diagnostics = {k: v for k, v in (d.diagnostics or {}).items() if k not in ("archived_by", "removed_at")}
        return d

    @staticmethod
    def _rename_allowed(d: Device) -> bool:
        """The adapter may (re)name a device unless the user gave it a name here."""
        inv = (d.diagnostics or {}).get("inventory") or {}
        return (
            d.name in DEFAULT_NAMES
            or d.name == d.stable_identity
            or d.name == default_name(d.kind, d.stable_identity)
            or d.name == f"Sensor {d.stable_identity[-6:]}"
            or d.name.startswith("Zigbee-apparaat ")
            or (inv.get("name") is not None and d.name == inv.get("name"))
        )

    # --- batch ---------------------------------------------------------------------------------

    async def handle_events(self, integ: Integration, evs: list) -> None:
        from shc_integrations.base import ButtonEvent, DeviceInfo, Reading, StateUpdate

        if not evs:
            return
        infos = [e for e in evs if isinstance(e, DeviceInfo)]
        rest = [e for e in evs if isinstance(e, Reading | StateUpdate)]
        presses = [e for e in evs if isinstance(e, ButtonEvent)]
        async with sessionmaker()() as db:
            home = await db.get(Home, integ.home_id)
            if home is None or home.active_to is not None:
                return  # archived home: nothing is stored any more (verbeterlijst 24)
            b = _Batch(integ, home)
            wanted = {e.device_identity for e in evs}
            for i in infos:
                g = (i.extra or {}).get("grouped_light")
                if g:
                    wanted.add(g)
            await self._load_devices(db, b, wanted)
            for ev in infos:
                await self._on_info(db, b, ev)
            for ev in rest:
                if isinstance(ev, Reading):
                    await self._on_reading(db, b, ev)
                else:
                    await self._on_state(db, b, ev)
            for ev in presses:
                await self.on_button(db, b, ev)
            await db.flush()
            for d in b.groups.values():
                await events.publish(db, "lights.updated", home.id, await group_view_for(db, d))
            if b.units:
                for z in (
                    await db.execute(select(ClimateZone).where(ClimateZone.unit_device_id.in_(b.units)))
                ).scalars():
                    await events.publish(db, "zone.updated", home.id, await views.zone_view(db, z, home))
            await db.commit()
        if b.queued:
            self.wake_commands.set()  # after the commit: the command rows exist now

    # --- inventory -----------------------------------------------------------------------------

    async def _on_info(self, db, b: _Batch, ev) -> None:
        from shc_integrations.base import should_upgrade_kind

        d = b.devices.get(ev.device_identity)
        now = datetime.now(UTC)
        if ev.removed:
            if d is None or user_archived(d):
                return
            if d.lifecycle != "archived":
                d.lifecycle = "archived"
                d.online = False
                d.diagnostics = {**(d.diagnostics or {}), "archived_by": "adapter", "removed_at": now.isoformat()}
                log.info("Apparaat %s is niet meer aanwezig op %s; gearchiveerd", d.name, b.integ.name)
            return
        if d is None:
            d = await self._device(db, b, ev.device_identity, ev.kind, ev.name)
        else:
            if user_archived(d):
                return
            d = await self._device(db, b, ev.device_identity, d.kind)
            if should_upgrade_kind(d.kind, ev.kind):
                log.info("Apparaat %s: type %s -> %s", d.name, d.kind, ev.kind)
                d.kind = ev.kind
            if ev.name and ev.name != d.name and self._rename_allowed(d):
                d.name = ev.name[:100]
        if ev.model:
            d.model = str(ev.model)[:100]
        if ev.firmware:
            d.firmware = str(ev.firmware)[:64]
        extra = dict(ev.extra or {})
        d.diagnostics = {
            **(d.diagnostics or {}),
            "inventory": {
                "name": ev.name,
                "kind": ev.kind,
                "model": ev.model,
                "firmware": ev.firmware,
                "extra": extra,
                "seen_at": now.isoformat(),
            },
        }
        st = dict(d.state or {})
        if ev.kind == "button":
            buttons = extra.get("buttons") or []
            st.update(
                buttons=buttons,
                bridge_rules=extra.get("bridge_rules") or st.get("bridge_rules") or [],
                events=sorted({e for btn in buttons for e in (btn.get("events") or [])}) or st.get("events"),
            )
        elif ev.kind == "scene":
            grp = extra.get("grouped_light")
            g = b.devices.get(grp) if grp else None
            st.update(group=extra.get("group"), grouped_light=grp, group_device_id=str(g.id) if g else None)
        elif ev.kind == "light_group":
            st.update(owner=extra.get("owner"), owner_type=extra.get("owner_type"), all_lights=extra.get("all_lights"))
        elif b.integ.provider == "zigbee":
            keys = ("friendly_name", "vendor", "description", "supported", "interview_completed", "power_source")
            st.update({k: extra[k] for k in keys if k in extra})
            if ev.kind == "zigbee_bridge":
                coord = ("coordinator_ieee", "zigbee2mqtt_version", "permit_join")
                st.update({k: extra[k] for k in coord if k in extra})
        d.state = {k: v for k, v in st.items() if v is not None or k == "group_device_id"}

    # --- readings ------------------------------------------------------------------------------

    def _reading_kind(self, integ: Integration, metric: str) -> str:
        if integ.provider == "zigbee":
            return "zigbee_sensor" if metric in SENSOR_METRICS else "zigbee_device"
        if integ.provider == "gree":
            return "ac_unit"
        if integ.provider == "homewizard":
            try:
                from shc_integrations.homewizard.adapter import product_info

                kind = product_info((integ.config or {}).get("product_type"))["kind"]
                return kind if kind != "unknown" else "meter"
            except Exception:
                return "meter"
        return "meter"

    async def _on_reading(self, db, b: _Batch, ev) -> None:
        from shc_integrations.base import should_upgrade_kind

        d = await self._device(db, b, ev.device_identity, self._reading_kind(b.integ, ev.metric))
        if user_archived(d):
            return
        metric = METRIC_MAP.get(ev.metric, ev.metric)
        if b.integ.provider == "zigbee" and metric in SENSOR_METRICS and should_upgrade_kind(d.kind, "zigbee_sensor"):
            d.kind = "zigbee_sensor"
        value, raw = calibrate(d, metric, ev.value)
        await ingest_reading(db, d.id, metric, value, ev.unit, ev.observed_at, str(ev.quality), ev.event_id, raw)
        if d.last_seen is None or ev.observed_at > d.last_seen:
            d.last_seen = ev.observed_at
        d.online = True
        if metric == "battery" and ev.value is not None:
            key = f"battery:{d.id}"
            if ev.value < BATTERY_LOW:
                await raise_alert(
                    db, d.home_id, key, "warn", f"Batterij {d.name} bijna leeg", f"{ev.value:.0f}% resterend", "zigbee"
                )
            else:
                await resolve_alert(db, key)

    # --- states --------------------------------------------------------------------------------

    async def _on_state(self, db, b: _Batch, ev) -> None:
        st = {k: v for k, v in (ev.state or {}).items() if k != "raw"}
        provider = b.integ.provider
        if provider == "gree":
            d = await self._device(db, b, ev.device_identity, "ac_unit", b.integ.name, reactivate=ev.online)
            if user_archived(d):
                return
            mode = "off" if st.get("power") is False else st.get("mode")
            d.state = {**(d.state or {}), **st, "mode": mode}
            caps = st.get("caps")
            if isinstance(caps, dict):
                d.capabilities = [*GREE_BASE_CAPS, *(c for c in ("fan", "swing") if caps.get(c))]
            if st.get("target_c") is not None:
                await ingest_reading(db, d.id, "target_c", st["target_c"], "°C", ev.observed_at)
            b.units.add(d.id)
        elif provider == "hue":
            typ = st.get("type")
            kind = {"grouped_light": "light_group", "light": "light", "button_device": "button"}.get(typ, "hue_device")
            d = await self._device(db, b, ev.device_identity, kind, st.get("name"), reactivate=ev.online)
            if user_archived(d):
                return
            from shc_integrations.base import should_upgrade_kind

            if should_upgrade_kind(d.kind, kind):
                d.kind = kind
            name = st.get("name")
            if name and name != d.name and self._rename_allowed(d):
                d.name = str(name)[:100]
            merged = {**(d.state or {}), **st}
            if typ == "button_device":
                buttons = st.get("buttons") or []
                merged["events"] = sorted({e for btn in buttons for e in (btn.get("events") or [])}) or merged.get(
                    "events"
                )
            d.state = merged
            if d.kind == "light_group":
                b.groups[d.id] = d
        elif provider == "homewizard":
            d = await self._device(db, b, ev.device_identity, self._reading_kind(b.integ, ""), reactivate=ev.online)
            if user_archived(d):
                return
            d.state = {**(d.state or {}), **st}
            if "relay_on" in st or "switch_lock" in st:
                await self._socket_alerts(db, d)
        elif provider == "zigbee":
            if ev.device_identity == COORDINATOR_IDENTITY:
                d = await self._device(
                    db, b, ev.device_identity, "zigbee_bridge", COORDINATOR_NAME, reactivate=ev.online
                )
            else:
                d = await self._device(
                    db, b, ev.device_identity, "zigbee_device", st.get("friendly_name"), reactivate=ev.online
                )
                if st.get("friendly_name") and self._rename_allowed(d) and st["friendly_name"] != d.stable_identity:
                    d.name = str(st["friendly_name"])[:100]
            if user_archived(d):
                return
            d.state = {**(d.state or {}), **st}
        else:
            d = await self._device(db, b, ev.device_identity, "device", ev.device_identity, reactivate=ev.online)
            if user_archived(d):
                return
            d.state = {**(d.state or {}), **st}
        d.online = ev.online
        if d.last_seen is None or ev.observed_at > d.last_seen:
            d.last_seen = ev.observed_at

    async def _socket_alerts(self, db, d: Device) -> None:
        """Verbeterlijst 15: a protected (boiler) socket that is off, or whose switch lock is off."""
        st = d.state or {}
        key_off, key_lock = f"relay_off:{d.id}", f"switch_lock:{d.id}"
        if st.get("relay_state") == "unsupported":
            await resolve_alert(db, key_off)
            await resolve_alert(db, key_lock)
            return
        roles, protected = await roles_of(db, d.id)
        is_protected = bool(protected) or bool(roles & DEFAULT_PROTECTED_ROLES)
        if is_protected and st.get("relay_on") is False:
            await raise_alert(
                db,
                d.home_id,
                key_off,
                "error",
                f"Stekker {d.name} staat uit",
                "Deze stekker is beveiligd als alleen-meten (boiler), maar meldt dat hij uit staat: de boiler krijgt "
                "geen stroom en er komt geen warm water. Zet de stekker weer aan met de knop op de stekker of in "
                "de HomeWizard Energy-app. Deze app schakelt de stekker nooit zelf.",
                "homewizard",
            )
        elif st.get("relay_on") is True or not is_protected:
            await resolve_alert(db, key_off)
        if is_protected and st.get("switch_lock") is False:
            await raise_alert(
                db,
                d.home_id,
                key_lock,
                "warn",
                f"Switch lock staat uit bij {d.name}",
                "Zet in de HomeWizard Energy-app bij deze stekker ‘Switch lock’ aan (instellingen van het "
                "apparaat). Dan kan de stekker niet meer worden uitgezet, ook niet per ongeluk met de knop op de "
                "stekker of vanuit een automatisering. Deze app schakelt de stekker nooit zelf.",
                "homewizard",
            )
        elif st.get("switch_lock") is True or not is_protected:
            await resolve_alert(db, key_lock)

    # --- buttons -------------------------------------------------------------------------------

    async def on_button(self, db, b: _Batch, ev) -> None:
        now = datetime.now(UTC)
        if ev.observed_at is not None and now - ev.observed_at > BUTTON_MAX_AGE:
            log.info("Knopgebeurtenis %s/%s genegeerd: ouder dan 10 s", ev.button, ev.event)
            return
        from shc_integrations.base import should_upgrade_kind

        dev = await self._device(db, b, ev.device_identity, "button", "Hue-knop")
        if user_archived(dev):
            return
        if should_upgrade_kind(dev.kind, "button"):
            dev.kind = "button"
        if ev.event_id:
            key = f"{b.integ.provider}:{ev.event_id}"
        else:
            at = ev.observed_at.isoformat() if ev.observed_at else now.isoformat()
            key = f"{b.integ.provider}:{ev.device_identity}:{ev.button}:{ev.event}:{at}"
        res = await db.execute(
            insert(ProcessedEvent)
            .values(event_key=key[:200], device_id=dev.id, at=now)
            .on_conflict_do_nothing(index_elements=["event_key"])
        )
        if not res.rowcount:
            log.debug("Knopgebeurtenis %s al verwerkt", key)
            return
        dev.state = {
            **(dev.state or {}),
            "last_event": {"button": ev.button, "event": ev.event, "at": (ev.observed_at or now).isoformat()},
        }
        dev.last_seen, dev.online = now, True
        maps = (
            await db.execute(
                select(ButtonMapping).where(
                    ButtonMapping.device_id == dev.id,
                    ButtonMapping.button == ev.button,
                    ButtonMapping.event == ev.event,
                    or_(ButtonMapping.enabled.is_(True), ButtonMapping.enabled.is_(None)),
                )
            )
        ).scalars()
        for m in list(maps):
            if m.mode == "combined" and not m.combined_confirmed:
                continue
            for step in mapping_steps(m.action):
                if step["type"] == "zone" and m.mode == "lights":
                    continue  # never run a climate step from a lights mapping
                if step["type"] == "light" and m.mode == "climate":
                    continue
                await self._run_step(db, b, dev, step)

    async def _run_step(self, db, b: _Batch, dev: Device, step: dict) -> None:
        actor = f"knop {dev.name}"
        if step["type"] == "zone":
            try:
                zone_id = uuid.UUID(str(step.get("zone_id")))
            except ValueError:
                return
            zone = await db.get(ClimateZone, zone_id)
            if zone is None or zone.home_id != dev.home_id:
                return
            try:
                async with db.begin_nested():
                    if step.get("action") == "resume":
                        await resume_zone(db, zone, b.home, actor, log_name=f"Knop {dev.name}")
                    else:
                        await request_zone_command(
                            db,
                            zone,
                            b.home,
                            mode=step.get("mode"),
                            target_c=step.get("target_c"),
                            fan=step.get("fan"),
                            override_minutes=step.get("override_minutes"),
                            source="button",
                            actor=actor,
                            log_name=f"Knop {dev.name}",
                        )
                        b.queued = True
            except CommandRejected as e:
                log.info("Knop %s: klimaatstap niet uitgevoerd: %s", dev.name, e.message)
                db.add(ZoneLog(zone_id=zone_id, kind="warn", message=f"Knop {dev.name}: niet uitgevoerd — {e.message}"))
            return
        try:
            target_id = uuid.UUID(str(step.get("device_id")))
        except ValueError:
            return
        target = await db.get(Device, target_id)
        if target is None or target.home_id != dev.home_id or target.lifecycle == "archived":
            return
        action, params = light_command(target, step)
        try:
            async with db.begin_nested():
                await queue_command(db, b.home.id, target, action, params, "button", actor)
            b.queued = True
        except PolicyDenied as e:
            log.warning("Knop %s: stap naar %s geweigerd: %s", dev.name, target.name, e.message)
