"""Climate engine rounds: read zones, decide (climate.decide), queue commands, log."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .. import climate as cl
from .. import events, views
from ..db import sessionmaker
from ..models import ClimateZone, CommandRecord, Device, Home, ZoneLog
from ..policy import DeviceFacts, PolicyDenied, check_command
from ..store import get_setting, roles_of


class ClimateMixin:
    # Provided by core.Worker.
    started_at: datetime
    wake_commands: asyncio.Event

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
                    from ..store import latest_reading

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
        from ..adaptive import advised_offset
        from ..models import AdaptiveModelVersion

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
