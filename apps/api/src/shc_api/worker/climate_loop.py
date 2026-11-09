"""Climate engine rounds: read zones, decide (climate.decide), queue commands, log.

Zones that share an outdoor unit are decided together, in one transaction under the same
advisory lock the API and the command executor use (``commanding.lock_outdoor_group``).
Zones whose unit already runs go first, so a running room keeps its mode and a room that
wants to start in a conflicting mode waits.

Besides deciding, each round: confirms or times out unconfirmed commands, runs the
watchdog (room does not move toward its target), and switches experiment profiles on and off
(the zone gets its own profile back between variants and afterwards). The zone log only gets
a line when the decision changes (``ClimateZone.last_decision``), not every minute. Old zone-log
lines are deleted by the retention round (worker/aggregates.py).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .. import climate as cl
from .. import events
from ..commanding import (
    experiment_apply_variant,
    experiment_restore_zone,
    lock_outdoor_group,
    publish_zone,
    queue_engine_command,
    zone_command_confirmed,
    zone_command_failed,
)
from ..db import sessionmaker
from ..models import (
    ClimateZone,
    ComfortProfile,
    CommandRecord,
    Device,
    Experiment,
    Home,
    SensorReading,
    ZoneLog,
)
from ..policy import PolicyDenied
from ..store import latest_reading, raise_alert, resolve_alert
from ..views.common import caps_of, effective_mode, other_zone_modes, schedule_entries, unit_state

log = logging.getLogger("shc.worker")

SENT_TIMEOUT = timedelta(minutes=2)  # a sent-but-unconfirmed command must show up in a readback within this
PENDING_STALE = timedelta(minutes=10)  # bookkeeping safety net for a command that never finished
SETTLE_MAX = timedelta(minutes=10)
FINAL_STATUSES = ("confirmed", "failed", "denied", "cancelled")


class ClimateMixin:
    # Provided by core.Worker.
    started_at: datetime
    wake_commands: asyncio.Event

    async def climate_round(self, home_ids: set[uuid.UUID] | None = None) -> None:
        """One engine round. ``home_ids`` limits it to some homes (tests, diagnostics)."""
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            q = (
                select(ClimateZone.id, ClimateZone.home_id, ClimateZone.outdoor_group)
                .join(Home, Home.id == ClimateZone.home_id)
                .where(Home.active_to.is_(None))
            )
            if home_ids:
                q = q.where(ClimateZone.home_id.in_(home_ids))
            rows = (await db.execute(q)).all()
        groups: dict[tuple, list[uuid.UUID]] = {}
        for r in rows:
            groups.setdefault((r.home_id, r.outdoor_group or f"zone:{r.id}"), []).append(r.id)
        for (home_id, _), zone_ids in groups.items():
            key = f"climate_error:{zone_ids[0]}"
            try:
                await self._group_round(home_id, zone_ids, now)
            except Exception as exc:  # one broken zone must not stop the others (nor the whole loop)
                log.exception("Klimaatronde faalde voor zones %s", zone_ids)
                await self._report_group_error(home_id, key, exc)
            else:
                if key in self._climate_errors:
                    await self._report_group_error(home_id, key, None)
        await self._experiments_round(now, home_ids)
        # Old zone-log lines are deleted by the retention round (worker/aggregates.py), with the
        # period set in the app ("Logboek klimaatregeling"), not here.

    @property
    def _climate_errors(self) -> set[str]:
        if not hasattr(self, "_climate_error_keys"):
            self._climate_error_keys: set[str] = set()
        return self._climate_error_keys

    async def _report_group_error(self, home_id: uuid.UUID, key: str, exc: Exception | None) -> None:
        async with sessionmaker()() as db:
            if exc is None:
                await resolve_alert(db, key)
                self._climate_errors.discard(key)
            else:
                await raise_alert(
                    db,
                    home_id,
                    key,
                    "error",
                    "Klimaatregeling loopt vast op een zone",
                    f"De automatische regeling slaat deze zone over door een fout: {str(exc)[:300]}",
                    "climate",
                )
                self._climate_errors.add(key)
            await db.commit()

    async def _group_round(self, home_id: uuid.UUID, zone_ids: list[uuid.UUID], now: datetime) -> None:
        async with sessionmaker()() as db:
            first = await db.get(ClimateZone, zone_ids[0])
            if first is None:
                return
            await lock_outdoor_group(db, first)
            zones = list((await db.execute(select(ClimateZone).where(ClimateZone.id.in_(zone_ids)))).scalars())
            home = await db.get(Home, home_id)
            if home is None or home.active_to is not None:
                return
            units: dict[uuid.UUID, Device | None] = {}
            running: dict[uuid.UUID, bool] = {}
            for z in zones:
                await db.refresh(z)
                units[z.id] = await db.get(Device, z.unit_device_id) if z.unit_device_id else None
                running[z.id] = (await effective_mode(db, units[z.id], now)) not in (None, "off")
            zones.sort(key=lambda z: (not running[z.id], z.name))
            for z in zones:
                await self._zone_round(db, z, home, units[z.id], now)
                await db.flush()  # the next zone of this outdoor unit sees what was just queued
            await db.commit()

    async def _zone_round(self, db, z: ClimateZone, home: Home, unit: Device | None, now: datetime) -> None:
        if unit is None:
            return  # sensor-only zone: nothing to regulate
        # Startup safety: wait until the unit state was read after this worker started.
        if unit.last_seen is None or unit.last_seen < self.started_at:
            return
        pol = cl.policy_with_defaults(z.policy)
        caps = caps_of(unit)
        us = unit_state(unit)
        in_flight, settling = await self._pending_state(db, z, unit, us, caps, now)
        sensor = None
        if z.sensor_device_id:
            r = await latest_reading(db, z.sensor_device_id, "temperature")
            sensor = cl.SensorValue(r.value if r else None, r.observed_at if r else None)
        base = dict(z.last_commanded or {})
        seen = base.get("mismatch_seen_at")
        readback_new = seen is None or (us.at is not None and us.at > datetime.fromisoformat(seen))
        zi = cl.ZoneInput(
            now=now,
            tz=ZoneInfo(home.timezone),
            policy=pol,
            caps=caps,
            control=z.control,
            desired_mode=z.desired_mode,
            desired_target_c=z.desired_target_c,
            override_until=z.override_until,
            sensor=sensor,
            unit=us,
            schedule=await schedule_entries(db, z.id),
            last_command_at=z.last_command_at,
            last_commanded=base,
            start_times=[datetime.fromisoformat(t) for t in (z.start_times or [])],
            other_zone_modes=await other_zone_modes(db, z, now),
            adaptive_offset_c=await self._adaptive_offset(db, z, sensor, home),
            pending=in_flight or settling,
            mismatch_count=z.mismatch_count or 0,
            readback_new=readback_new,
            correction_c=z.correction_c or 0.0,
        )
        d = cl.decide(zi)

        prev_control, prev_until = z.control, z.override_until
        z.control = d.control
        z.override_until = d.override_until if d.control == "manual" else None
        if d.override_source:
            z.override_source = d.override_source
        elif d.control in ("schedule", "oem_fallback"):
            z.override_source = None
        if d.baseline is not None:
            z.last_commanded = {**d.baseline, "at": now.isoformat()}
            if d.baseline.get("mode") not in (None, "off"):
                z.desired_mode = d.baseline["mode"]
        elif d.mismatch_count > (z.mismatch_count or 0) and us.at is not None:
            z.last_commanded = {**base, "mismatch_seen_at": us.at.isoformat()}
        z.mismatch_count = d.mismatch_count
        if d.correction_c is not None:
            z.correction_c = d.correction_c

        sent = None
        reason = d.reason
        if d.send:
            try:
                sent = await queue_engine_command(db, z, home, unit, d.send, starting=d.starting, now=now)
            except PolicyDenied as e:
                reason = "policy_denied"
                if z.last_decision != f"{d.control}:{reason}":
                    db.add(ZoneLog(zone_id=z.id, kind="warn", message=e.message))
            else:
                self.wake_commands.set()

        # Log only when the decision changes (or a command goes out), not every round.
        signature = f"{d.control}:{reason}"[:32]
        changed = signature != z.last_decision
        z.last_decision = signature
        if changed or sent is not None:
            for line in d.log:
                db.add(ZoneLog(zone_id=z.id, kind="engine", message=line))
        if sent is not None:
            db.add(ZoneLog(zone_id=z.id, kind="engine", message=self._describe(d)))

        await self._watchdog(db, z, home, d, us, sensor, pol, now)

        if changed or sent is not None or prev_control != z.control or prev_until != z.override_until:
            await publish_zone(db, z, home)

    async def _pending_state(self, db, z: ClimateZone, unit: Device, us: cl.UnitState, caps, now) -> tuple[bool, bool]:
        """(command in flight, settling after one). Confirms late readbacks and times out unconfirmed commands."""
        if z.pending_command_id is not None:
            cmd = await db.get(CommandRecord, z.pending_command_id)
            if cmd is None or cmd.status in FINAL_STATUSES:
                z.pending_command_id, z.pending_since = None, now
            elif cmd.status == "sent":
                fresh = us.at is not None and us.at > cmd.updated_at
                fan = (unit.state or {}).get("fan")
                if fresh and us.online and cl.matches_command(cmd.params, us.mode, us.target_c, fan):
                    cmd.status, cmd.detail, cmd.updated_at = "confirmed", "Bevestigd bij een latere uitlezing", now
                    await zone_command_confirmed(db, z, cmd, unit, now)
                    await events.publish(
                        db, "command.updated", cmd.home_id, {"id": cmd.id, "status": cmd.status, "detail": cmd.detail}
                    )
                elif now - cmd.updated_at > SENT_TIMEOUT:
                    cmd.status = "failed"
                    cmd.detail = "De unit liet de gevraagde stand niet zien"
                    cmd.updated_at = now
                    if not await zone_command_failed(db, z, cmd, now):
                        await events.publish(
                            db,
                            "command.updated",
                            cmd.home_id,
                            {"id": cmd.id, "status": cmd.status, "detail": cmd.detail},
                        )
                    else:
                        self.wake_commands.set()
            elif z.pending_since is not None and now - z.pending_since > PENDING_STALE:
                z.pending_command_id, z.pending_since = None, now
        in_flight = z.pending_command_id is not None
        settling = False
        if not in_flight and z.pending_since is not None:
            if us.at is not None and us.at > z.pending_since:
                settling = True  # the one readback after a command: skip detection once more
                z.pending_since = None
            elif now - z.pending_since > SETTLE_MAX:
                z.pending_since = None
            else:
                settling = True
        return in_flight, settling

    async def _watchdog(self, db, z: ClimateZone, home: Home, d: cl.Decision, us, sensor, pol: dict, now) -> None:
        """Alert when the room does not move toward its target while the unit runs (plan §5 'watchdog').

        The room temperature comes from the zone's own sensor or, for a zone without one, from the unit's
        own room sensor (the same temperature the zone view shows): the engine does not correct the setpoint
        then, but a unit that does not really heat or cool is still noticed (verbeterlijst 45)."""
        key = f"watchdog:{z.id}"
        if d.reason in ("pending", "checking_change", "unit_offline"):
            return  # transient: keep the window running
        minutes = int(pol.get("watchdog_minutes") or 0)
        mode = us.mode if us.mode in cl.CORRECTED_MODES else None
        target = d.target_base_c if d.control in ("schedule", "oem_fallback") else us.target_c
        source = z.sensor_device_id or z.unit_device_id
        if z.sensor_device_id:
            temp, valid = (sensor.value if sensor else None), d.sensor_valid
        else:
            own = await latest_reading(db, z.unit_device_id, "temperature") if minutes > 0 else None
            room = cl.SensorValue(own.value if own else None, own.observed_at if own else None)
            temp, valid = room.value, cl.sensor_status(room, now, pol)[0]
        if (
            minutes <= 0
            or source is None
            or not valid
            or mode is None
            or d.mode not in (None, mode)
            or not cl.watchdog_needed(mode, target, temp, cl.effective_band(pol))
        ):
            if z.watchdog_since is not None:
                z.watchdog_since = None
            await resolve_alert(db, key)
            return
        if z.watchdog_since is None:
            z.watchdog_since = now
            return
        if now - z.watchdog_since < timedelta(minutes=minutes):
            return
        start = (
            await db.execute(
                select(SensorReading)
                .where(
                    SensorReading.device_id == source,
                    SensorReading.metric == "temperature",
                    SensorReading.observed_at <= z.watchdog_since,
                    SensorReading.value.is_not(None),
                )
                .order_by(SensorReading.observed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        z.watchdog_since = now  # next window
        if start is None or start.value is None:
            return
        progress = cl.watchdog_progress(mode, start.value, temp)
        if progress < float(pol.get("watchdog_min_progress_c") or 0.3):
            verb = "verwarmt" if mode in cl.HEATING_MODES else "koelt"
            measured = "" if z.sensor_device_id else " (gemeten door de airco zelf)"
            await raise_alert(
                db,
                home.id,
                key,
                "warn",
                f"{z.name}: temperatuur reageert niet",
                f"De airco {verb} al {minutes} minuten, maar de temperatuur{measured} ging van {start.value:.1f} "
                f"naar {temp:.1f} °C (doel {target:.1f} °C). Controleer ramen en deuren, het filter en of de "
                "airco echt draait.",
                "climate",
            )
            db.add(ZoneLog(zone_id=z.id, kind="warn", message="Waakhond: de kamer reageert niet op de airco."))
        else:
            await resolve_alert(db, key)

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
        entry = cl.scheduled_entry(await schedule_entries(db, z.id), datetime.now(UTC), ZoneInfo(home.timezone))
        if m is None or entry is None:
            return 0.0
        return advised_offset(m.params, entry.target_c, sensor.value, cl.policy_with_defaults(z.policy)["band_c"])

    # --- experiments ----------------------------------------------------------------

    async def _experiments_round(self, now: datetime, home_ids: set[uuid.UUID] | None = None) -> None:
        """Apply the active variant's profile of auto-apply experiments; pause on override or missing data.
        Paused ones are watched too: their variant profile goes off when its time is over, and they are
        finished after the last variant (item 45), so a pause never leaves a profile on for good."""
        async with sessionmaker()() as db:
            q = select(Experiment).where(
                Experiment.auto_apply.is_(True),
                Experiment.status.in_(("defined", "running", "paused")),
            )
            if home_ids:
                q = q.where(Experiment.home_id.in_(home_ids))
            for e in (await db.execute(q)).scalars().all():
                try:
                    async with db.begin_nested():
                        if e.status == "paused" or e.paused_reason is not None:
                            await self._paused_experiment_step(db, e, now)
                        else:
                            await self._experiment_step(db, e, now)
                except Exception:
                    log.exception("Experiment %s kon niet worden bijgewerkt", e.id)
            await db.commit()

    async def _paused_experiment_step(self, db, e: Experiment, now: datetime) -> None:
        """A paused experiment (by the worker or a user) applies nothing new, but lets go of its variant
        profile once that variant's time is over, and finishes after the last variant."""
        variants = e.variants or []
        if not variants:
            return
        spans = [(datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])) for v in variants]
        zone_id = e.zone_id or (uuid.UUID(variants[0]["zone_id"]) if variants[0].get("zone_id") else None)
        zone = await db.get(ClimateZone, zone_id) if zone_id else None
        home = await db.get(Home, e.home_id)
        if now >= max(end for _, end in spans):
            e.status, e.current_variant = "finished", None
            if zone is not None:
                db.add(ZoneLog(zone_id=zone.id, kind="config", message=f"Experiment '{e.name}' afgerond (gepauzeerd)."))
                await self._experiment_let_go(db, e, zone, home)
            await resolve_alert(db, f"experiment:{e.id}")
            return
        cv = e.current_variant
        if zone is None or cv is None:
            return
        if not (0 <= cv < len(spans) and spans[cv][0] <= now < spans[cv][1]):
            # The paused variant's time is over: its profile goes off; resuming applies the variant
            # that is due then (or nothing between variants).
            e.current_variant = None
            await self._experiment_let_go(db, e, zone, home)

    async def _experiment_step(self, db, e: Experiment, now: datetime) -> None:
        variants = e.variants or []
        if not variants:
            return
        spans = [(datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])) for v in variants]
        zone_id = e.zone_id or (uuid.UUID(variants[0]["zone_id"]) if variants[0].get("zone_id") else None)
        zone = await db.get(ClimateZone, zone_id) if zone_id else None
        if zone is None:
            e.status, e.paused_reason = "paused", "De zone van dit experiment bestaat niet meer."
            return
        home = await db.get(Home, e.home_id)
        if now >= max(end for _, end in spans):
            e.status, e.current_variant = "finished", None
            db.add(ZoneLog(zone_id=zone.id, kind="config", message=f"Experiment '{e.name}' afgerond."))
            await self._experiment_let_go(db, e, zone, home)
            await resolve_alert(db, f"experiment:{e.id}")
            return
        idx = next((i for i, (s, end) in enumerate(spans) if s <= now < end), None)
        if idx is None:
            # Before the first variant, or between two: no variant profile stays on (item 45).
            if e.current_variant is not None:
                e.current_variant = None
                await self._experiment_let_go(db, e, zone, home)
            return
        reason = None
        if zone.control in ("manual", "off"):
            reason = "De zone werd handmatig bediend; experiment gepauzeerd."
        else:
            pol = cl.policy_with_defaults(zone.policy)
            t = await latest_reading(db, zone.sensor_device_id or zone.unit_device_id, "temperature")
            if t is None or (now - t.observed_at).total_seconds() > pol["stale_after_s"]:
                reason = "Temperatuurmetingen ontbreken; experiment gepauzeerd."
        if reason:
            e.status, e.paused_reason = "paused", reason
            db.add(ZoneLog(zone_id=zone.id, kind="warn", message=f"Experiment '{e.name}': {reason}"))
            await raise_alert(
                db,
                e.home_id,
                f"experiment:{e.id}",
                "info",
                f"Experiment '{e.name}' gepauzeerd",
                reason + " Hervat het experiment op de pagina Optimalisatie.",
                "climate",
            )
            return
        if e.current_variant != idx:
            v = variants[idx]
            pid = v.get("profile_id")
            profile = await db.get(ComfortProfile, uuid.UUID(pid)) if pid else None
            if pid and profile is None:
                e.status, e.paused_reason = "paused", f"Het profiel van variant '{v['label']}' bestaat niet meer."
                return
            if profile is not None:
                await lock_outdoor_group(db, zone)
                await db.refresh(zone)
                await experiment_apply_variant(db, e, zone, profile, "experiment")
                if home is not None:
                    await db.flush()
                    await publish_zone(db, zone, home)
            e.current_variant = idx
            e.status = "running"
            db.add(ZoneLog(zone_id=zone.id, kind="config", message=f"Experiment '{e.name}': variant '{v['label']}'"))

    async def _experiment_let_go(self, db, e: Experiment, zone: ClimateZone, home: Home | None) -> None:
        """Switch the variant profile off: the zone gets its own profile and schedule back."""
        if await experiment_restore_zone(db, e, zone, "experiment") == "restored" and home is not None:
            await db.flush()
            await publish_zone(db, zone, home)

    @staticmethod
    def _describe(d: cl.Decision) -> str:
        s = d.send or {}
        if s.get("mode") == "off":
            return "Unit uit volgens schema"
        why = {"schedule": "schema", "fallback_schedule": "schema (sensor ongeldig, geen bijsturing)"}.get(
            d.reason, d.reason
        )
        target = f"Setpoint {s['target_c']:g}° " if s.get("target_c") is not None else ""
        return f"{target}modus {s.get('mode')} · {why}"
