"""Climate control engine (plan §5). Pure functions, no I/O, fully unit-tested.

Decision priority:
 1. hardware/safety limits (capabilities, shared outdoor unit, start limits)
 2. manual override (app, remote control, deliberate "off")
 3. sensor validity (stale/implausible -> stop external correction, OEM keeps control)
 4. schedule / profile
 5. adaptive advice (bounded offset, opt-in)

The engine never toggles the unit on/off to hit comfort; it only nudges the setpoint
within bounds so the inverter can modulate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

HEATING_MODES = {"heat"}
COOLING_MODES = {"cool", "dry"}
ACTIVE_MODES = {"heat", "cool", "dry", "fan", "auto"}

DEFAULT_POLICY = {
    "band_c": 0.5,
    "min_interval_s": 900,
    "max_step_c": 1.0,
    "max_starts_per_hour": 3,
    "stale_after_s": 1800,
    "plausible_min_c": 5.0,
    "plausible_max_c": 40.0,
    "max_offset_c": 2.0,
    "override_default_minutes": 120,
}


def policy_with_defaults(p: dict | None) -> dict:
    return {**DEFAULT_POLICY, **(p or {})}


@dataclass(slots=True)
class Capabilities:
    modes: tuple[str, ...] = ("off", "heat", "cool", "dry", "fan", "auto")
    min_c: float = 16.0
    max_c: float = 30.0
    step_c: float = 0.5  # Gree units accept whole degrees on most models; adapter reports real step


@dataclass(slots=True)
class SensorValue:
    value: float | None
    at: datetime | None


@dataclass(slots=True)
class UnitState:
    online: bool
    mode: str | None  # current mode as read back from the unit ("off" when power off)
    target_c: float | None
    at: datetime | None = None


@dataclass(slots=True)
class ScheduleEntry:
    days: tuple[int, ...]
    start: str  # "HH:MM"
    target_c: float
    mode: str | None = None
    label: str = ""


@dataclass(slots=True)
class ZoneInput:
    now: datetime
    tz: tzinfo
    policy: dict
    caps: Capabilities
    control: str  # schedule | manual | off | oem_fallback
    desired_mode: str
    desired_target_c: float | None
    override_until: datetime | None
    sensor: SensorValue | None
    unit: UnitState
    schedule: list[ScheduleEntry]
    last_command_at: datetime | None
    last_commanded: dict  # {"mode":..., "target_c":...} last values we sent
    start_times: list[datetime]
    other_zone_modes: list[str] = field(default_factory=list)  # same outdoor group, current modes
    adaptive_offset_c: float = 0.0  # from active adaptive model (opt-in), already bounded


@dataclass(slots=True)
class Decision:
    control: str
    send: dict | None  # {"mode":..., "target_c":...} or None
    reason: str
    notice: str | None = None
    override_until: datetime | None = None
    override_source: str | None = None
    sensor_valid: bool = True
    log: list[str] = field(default_factory=list)


# --- helpers ------------------------------------------------------------------


def blocked_modes(caps: Capabilities, other_zone_modes: list[str]) -> list[dict]:
    """Modes this zone may not use now because a zone on the same outdoor unit runs the opposite."""
    out = []
    others = set(other_zone_modes)
    if others & HEATING_MODES:
        for m in ("cool", "dry"):
            if m in caps.modes:
                out.append(
                    {
                        "mode": m,
                        "reason": "Een andere ruimte verwarmt; de buitenunit kan niet tegelijk koelen.",
                    }
                )
    if others & COOLING_MODES and "heat" in caps.modes:
        out.append(
            {"mode": "heat", "reason": "Een andere ruimte koelt; de buitenunit kan niet tegelijk verwarmen."}
        )
    return out


def validate_request(
    caps: Capabilities, other_zone_modes: list[str], mode: str | None, target_c: float | None
) -> str | None:
    """Return a Dutch error message when a requested change is not allowed, else None."""
    if mode is not None:
        if mode not in caps.modes:
            return f"Modus '{mode}' wordt door deze unit niet ondersteund."
        for b in blocked_modes(caps, other_zone_modes):
            if b["mode"] == mode:
                return b["reason"]
    if target_c is not None and not (caps.min_c <= target_c <= caps.max_c):
        return f"Doeltemperatuur moet tussen {caps.min_c:g} en {caps.max_c:g} °C liggen."
    return None


def sensor_status(sensor: SensorValue | None, now: datetime, policy: dict) -> tuple[bool, str | None]:
    if sensor is None or sensor.value is None or sensor.at is None:
        return False, "Geen sensorwaarde beschikbaar; de airco regelt op de eigen sensor."
    age = (now - sensor.at).total_seconds()
    if age > policy["stale_after_s"]:
        return False, f"Sensor verouderd ({int(age // 60)} min); externe bijsturing gepauzeerd."
    if not (policy["plausible_min_c"] <= sensor.value <= policy["plausible_max_c"]):
        return False, "Sensorwaarde onwaarschijnlijk; externe bijsturing gepauzeerd."
    return True, None


def scheduled_entry(schedule: list[ScheduleEntry], now: datetime, tz: tzinfo) -> ScheduleEntry | None:
    """Active block at `now` in local time; the latest block that started, wrapping over the week."""
    if not schedule:
        return None
    local = now.astimezone(tz)
    best: tuple[int, ScheduleEntry] | None = None
    minute_of_week = local.weekday() * 1440 + local.hour * 60 + local.minute
    for e in schedule:
        h, m = (int(x) for x in e.start.split(":"))
        for d in e.days:
            start = d * 1440 + h * 60 + m
            ago = (minute_of_week - start) % (7 * 1440)
            if best is None or ago < best[0]:
                best = (ago, e)
    return best[1] if best else None


def next_change(
    schedule: list[ScheduleEntry], now: datetime, tz: tzinfo
) -> tuple[datetime, ScheduleEntry] | None:
    if not schedule:
        return None
    local = now.astimezone(tz)
    minute_of_week = local.weekday() * 1440 + local.hour * 60 + local.minute
    best: tuple[int, ScheduleEntry] | None = None
    for e in schedule:
        h, m = (int(x) for x in e.start.split(":"))
        for d in e.days:
            start = d * 1440 + h * 60 + m
            ahead = (start - minute_of_week) % (7 * 1440) or 7 * 1440
            if best is None or ahead < best[0]:
                best = (ahead, e)
    assert best is not None
    at = (local.replace(second=0, microsecond=0) + timedelta(minutes=best[0])).astimezone(now.tzinfo)
    return at, best[1]


def _round_step(v: float, step: float) -> float:
    return round(round(v / step) * step, 2)


def _starts_last_hour(start_times: list[datetime], now: datetime) -> int:
    return sum(1 for t in start_times if now - t < timedelta(hours=1))


def detect_external_change(unit: UnitState, last_commanded: dict, tolerance_c: float = 0.25) -> bool:
    """True when the unit's state differs from what we last sent: someone used the remote / OEM app."""
    if not unit.online or not last_commanded or unit.mode is None:
        return False
    if last_commanded.get("mode") and unit.mode != last_commanded["mode"]:
        return True
    lt = last_commanded.get("target_c")
    if (
        lt is not None
        and unit.target_c is not None
        and unit.mode != "off"
        and abs(unit.target_c - lt) > tolerance_c
    ):
        return True
    return False


# --- main decision ---------------------------------------------------------------


def decide(z: ZoneInput) -> Decision:
    p = z.policy
    now = z.now
    log: list[str] = []
    valid, notice = sensor_status(z.sensor, now, p)

    if not z.unit.online:
        return Decision(
            z.control,
            None,
            "unit_offline",
            "Airco niet bereikbaar; laatste instelling blijft actief. "
            "Gebruik zo nodig de afstandsbediening.",
            sensor_valid=valid,
        )

    # 2. manual override & external changes (remote control) ----------------------------
    control = z.control
    override_until = z.override_until
    if control == "manual" and override_until and now >= override_until:
        control = "schedule"
        override_until = None
        log.append("Handmatige periode verlopen, terug naar schema.")
    if control in ("schedule", "oem_fallback") and detect_external_change(z.unit, z.last_commanded):
        if z.unit.mode == "off":
            # Deliberate off: never auto-reactivate (plan §5).
            log.append("Unit met afstandsbediening uitgezet; automaat blijft uit tot je hervat.")
            return Decision(
                "off", None, "external_off", "Uitgezet met afstandsbediening.", log=log, sensor_valid=valid
            )
        until = now + timedelta(minutes=p["override_default_minutes"])
        nc = next_change(z.schedule, now, z.tz)
        if nc and nc[0] < until:
            until = nc[0]
        log.append("Wijziging via afstandsbediening gedetecteerd; automaat wacht.")
        return Decision(
            "manual",
            None,
            "external_override",
            "Handmatig bediend; automaat wacht.",
            override_until=until,
            override_source="remote_control",
            log=log,
            sensor_valid=valid,
        )
    if control in ("manual", "off"):
        return Decision(
            control,
            None,
            "override_active",
            None if control == "manual" else "Uitgezet; hervat om " "het schema te volgen.",
            override_until=override_until,
            log=log,
            sensor_valid=valid,
        )

    # 4. schedule -------------------------------------------------------------------------------
    entry = scheduled_entry(z.schedule, now, z.tz)
    mode = (entry.mode if entry and entry.mode else z.desired_mode) or "off"
    base_target = entry.target_c if entry else z.desired_target_c
    if mode == "off" or base_target is None:
        send = None
        if z.unit.mode not in (None, "off") and z.last_commanded.get("mode") not in (None, "off"):
            send = {"mode": "off"}
        return Decision("schedule", send, "schedule_off", None, log=log, sensor_valid=valid)

    # 1. safety: shared outdoor unit
    for b in blocked_modes(z.caps, z.other_zone_modes):
        if b["mode"] == mode:
            return Decision("schedule", None, "mode_blocked", b["reason"], log=log, sensor_valid=valid)

    # 3. sensor validity: no external correction, just the scheduled target on the unit's own sensor
    target = base_target
    out_control = "schedule"
    if valid:
        assert z.sensor and z.sensor.value is not None
        err = base_target - z.sensor.value
        if mode in COOLING_MODES:
            err = -err
        # 5. bounded correction: offset the unit setpoint when the room is outside the band
        current_offset = (z.last_commanded.get("target_c") or base_target) - base_target
        if abs(err) > p["band_c"] / 2:
            step = max(-p["max_step_c"], min(p["max_step_c"], err))
            sign = 1 if mode not in COOLING_MODES else -1
            new_offset = current_offset + sign * step * 0.5
        else:
            new_offset = current_offset
        new_offset += z.adaptive_offset_c
        new_offset = max(-p["max_offset_c"], min(p["max_offset_c"], new_offset))
        target = base_target + new_offset
    else:
        out_control = "oem_fallback"

    # hardware limits + step + max jump
    target = max(z.caps.min_c, min(z.caps.max_c, target))
    prev = z.last_commanded.get("target_c") if z.last_commanded.get("mode") == mode else None
    if prev is not None and abs(target - prev) > p["max_step_c"]:
        target = prev + (p["max_step_c"] if target > prev else -p["max_step_c"])
        log.append("Setpoint-sprong begrensd.")
    target = _round_step(target, z.caps.step_c)

    want = {"mode": mode, "target_c": target}
    same_mode = z.unit.mode == mode
    same_target = z.unit.target_c is not None and abs(z.unit.target_c - target) < z.caps.step_c / 2
    if same_mode and same_target:
        return Decision(out_control, None, "no_change", notice, log=log, sensor_valid=valid)

    # rate limits
    if z.last_command_at and (now - z.last_command_at).total_seconds() < p["min_interval_s"] and same_mode:
        log.append("Geen wijziging: minimale tijd tussen wijzigingen nog niet verstreken.")
        return Decision(out_control, None, "min_interval", notice, log=log, sensor_valid=valid)
    if not same_mode and z.unit.mode in (None, "off"):
        if _starts_last_hour(z.start_times, now) >= p["max_starts_per_hour"]:
            log.append("Geen start: maximum aantal starts per uur bereikt.")
            return Decision(
                out_control,
                None,
                "max_starts",
                "Maximum aantal starts per uur bereikt.",
                log=log,
                sensor_valid=valid,
            )

    return Decision(
        out_control, want, "schedule" if valid else "fallback_schedule", notice, log=log, sensor_valid=valid
    )
