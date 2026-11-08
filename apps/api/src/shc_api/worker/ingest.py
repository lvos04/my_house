"""Adapter events -> devices, readings, Hue scenes and button presses."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from .. import events, views
from ..db import sessionmaker
from ..models import ButtonMapping, ClimateZone, CommandRecord, Device, Home, Integration
from ..store import ingest_reading, raise_alert, resolve_alert

METRIC_MAP = {"unit_temperature": "temperature"}  # Gree internal sensor -> unit "temperature"
BATTERY_LOW = 15


class IngestMixin:
    # Provided by core.Worker.
    adapters: dict[uuid.UUID, object]
    wake_commands: asyncio.Event

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
