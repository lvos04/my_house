"""Climate control engine (plan §5). Pure functions, no I/O, fully unit-tested.

Decision priority:
 1. hardware/safety limits (capabilities, shared outdoor unit, start limits)
 2. manual override (app, remote control, deliberate "off")
 3. sensor validity (stale/implausible -> stop external correction, OEM keeps control)
 4. schedule / profile
 5. adaptive advice (bounded offset, opt-in)

The engine never toggles the unit on/off to hit comfort; it only nudges the setpoint
within bounds so the inverter can modulate.

Command tracking (verbeterlijst 10/11): ``last_commanded`` is the *baseline*: the unit
state we last saw confirmed (after our own command, or adopted after a change with the
remote control). It is never set from a command that was merely queued. External-change
detection is skipped while a command is pending (and for one readback after it), and a
difference must be seen in ``MISMATCH_READBACKS`` consecutive readbacks.

Schedule blocks without a mode (verbeterlijst 12) keep the mode the unit runs in now and
never start a unit that is off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

HEATING_MODES = {"heat"}
COOLING_MODES = {"cool", "dry"}
CORRECTED_MODES = {"heat", "cool", "dry"}  # modes where the external sensor nudges the setpoint
ACTIVE_MODES = {"heat", "cool", "dry", "fan", "auto"}
ALL_MODES = ("off", "heat", "cool", "dry", "fan", "auto")
ENERGY_PRIORITIES = ("comfort", "balanced", "saving")

# A unit/baseline difference must be seen in this many consecutive readbacks before it
# counts as a change made with the remote control.
MISMATCH_READBACKS = 2

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
    "energy_priority": "balanced",  # comfort | balanced | saving
    "watchdog_minutes": 60,  # 0 = off
    "watchdog_min_progress_c": 0.3,
}

# Energy priority -> (band factor, share of max_offset allowed in the energy-costly direction)
PRIORITY_RULES = {
    "comfort": (0.75, 1.0),
    "balanced": (1.0, 1.0),
    "saving": (1.5, 0.5),
}


def policy_with_defaults(p: dict | None) -> dict:
    out = {**DEFAULT_POLICY, **(p or {})}
    if out.get("energy_priority") not in ENERGY_PRIORITIES:
        out["energy_priority"] = "balanced"
    return out


def effective_band(policy: dict) -> float:
    """Comfort band after the energy priority: comfort reacts sooner, saving tolerates more."""
    factor, _ = PRIORITY_RULES[policy.get("energy_priority", "balanced")]
    return policy["band_c"] * factor


def correction_limits(policy: dict, mode: str) -> tuple[float, float]:
    """(min, max) setpoint correction. 'saving' halves the energy-costly direction."""
    _, costly_share = PRIORITY_RULES[policy.get("energy_priority", "balanced")]
    m = policy["max_offset_c"]
    if mode in HEATING_MODES:  # raising the setpoint costs energy
        return -m, m * costly_share
    if mode in COOLING_MODES:  # lowering the setpoint costs energy
        return -m * costly_share, m
    return -m, m


@dataclass(slots=True)
class Capabilities:
    modes: tuple[str, ...] = ALL_MODES
    min_c: float = 16.0
    max_c: float = 30.0
    step_c: float = 1.0  # most Gree units accept whole degrees; the adapter reports the real step
    fan: bool | None = None  # None = not reported by the adapter
    swing: bool | None = None


@dataclass(slots=True)
class SensorValue:
    value: float | None
    at: datetime | None


@dataclass(slots=True)
class UnitState:
    online: bool
    mode: str | None  # current mode as read back from the unit ("off" when power off)
    target_c: float | None
    at: datetime | None = None  # time of the readback


@dataclass(slots=True)
class ScheduleEntry:
    days: tuple[int, ...]
    start: str  # "HH:MM"
    target_c: float
    mode: str | None = None  # None = keep the mode the unit runs in; never starts an off unit
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
    last_commanded: dict  # baseline: {"mode":..., "target_c":...} last confirmed/adopted unit state
    start_times: list[datetime]
    other_zone_modes: list[str] = field(default_factory=list)  # same outdoor group, effective modes
    adaptive_offset_c: float = 0.0  # from active adaptive model (opt-in), already bounded
    pending: bool = False  # a command is in flight, or we wait for the first readback after one
    mismatch_count: int = 0  # consecutive readbacks that differed from the baseline so far
    readback_new: bool = True  # the unit readback is newer than the last one counted as a mismatch
    correction_c: float = 0.0  # stored setpoint correction (°C, unrounded)


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
    baseline: dict | None = None  # new baseline to store (observed unit state); None = keep
    mismatch_count: int = 0
    correction_c: float | None = None  # new correction to store; None = keep
    mode: str | None = None  # mode the zone is regulated in (watchdog)
    target_base_c: float | None = None  # scheduled target before correction (watchdog, view)
    starting: bool = False  # the command starts a unit that is off


# --- helpers ------------------------------------------------------------------


def modes_conflict(mode: str | None, other: str | None) -> bool:
    """True when two indoor units on one outdoor unit cannot run these modes together.

    Heating conflicts with cooling/drying; "auto" may do either, so it conflicts with both.
    Fan-only and off never conflict.
    """
    a, b = mode, other
    if a in (None, "off", "fan") or b in (None, "off", "fan"):
        return False
    if a == "auto":
        return b in HEATING_MODES | COOLING_MODES
    if b == "auto":
        return a in HEATING_MODES | COOLING_MODES
    return (a in HEATING_MODES and b in COOLING_MODES) or (a in COOLING_MODES and b in HEATING_MODES)


def _conflict_reason(mode: str, other: str) -> str:
    if other == "auto":
        return "Een andere ruimte staat op automatisch; de buitenunit kan niet tegelijk verwarmen en koelen."
    if other in HEATING_MODES:
        if mode == "auto":
            return "Een andere ruimte verwarmt; automatisch kan ook gaan koelen. Kies verwarmen."
        return "Een andere ruimte verwarmt; de buitenunit kan niet tegelijk koelen."
    if mode == "auto":
        return "Een andere ruimte koelt; automatisch kan ook gaan verwarmen. Kies koelen."
    return "Een andere ruimte koelt; de buitenunit kan niet tegelijk verwarmen."


def blocked_modes(caps: Capabilities, other_zone_modes: list[str]) -> list[dict]:
    """Modes this zone may not use now because a zone on the same outdoor unit runs a conflicting mode."""
    out = []
    for m in caps.modes:
        for o in other_zone_modes:
            if modes_conflict(m, o):
                out.append({"mode": m, "reason": _conflict_reason(m, o)})
                break
    return out


def validate_request(
    caps: Capabilities, other_zone_modes: list[str], mode: str | None, target_c: float | None
) -> str | None:
    """Return a Dutch error message when a requested change is not allowed, else None."""
    if mode is not None and mode != "off":
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


def next_change(schedule: list[ScheduleEntry], now: datetime, tz: tzinfo) -> tuple[datetime, ScheduleEntry] | None:
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


def round_to_step(v: float, step: float, prefer_up: bool = True) -> float:
    """Round to the unit's setpoint step. Exact halves go up (or down with prefer_up=False),
    never to "even" like Python's round(), so a correction is not swallowed by rounding."""
    q = v / step
    lo = math.floor(q + 1e-9)
    frac = q - lo
    if abs(frac - 0.5) < 1e-6:
        n = lo + 1 if prefer_up else lo
    elif frac > 0.5:
        n = lo + 1
    else:
        n = lo
    return round(n * step, 2)


# Kept for callers that still use the old private name.
def _round_step(v: float, step: float) -> float:
    return round_to_step(v, step)


def _starts_last_hour(start_times: list[datetime], now: datetime) -> int:
    return sum(1 for t in start_times if now - t < timedelta(hours=1))


def observed_state(unit: UnitState) -> dict:
    """The unit state as a baseline dict."""
    out: dict = {"mode": unit.mode}
    if unit.target_c is not None:
        out["target_c"] = unit.target_c
    return out


def detect_external_change(unit: UnitState, last_commanded: dict, tolerance_c: float = 0.25) -> bool:
    """True when the unit's state differs from the baseline: someone used the remote / OEM app.

    Fan speed is not compared: the engine never sets it, so a fan change does not pause the schedule.
    """
    if not unit.online or not last_commanded or unit.mode is None:
        return False
    if last_commanded.get("mode") and unit.mode != last_commanded["mode"]:
        return True
    lt = last_commanded.get("target_c")
    if lt is not None and unit.target_c is not None and unit.mode != "off" and abs(unit.target_c - lt) > tolerance_c:
        return True
    return False


def matches_command(params: dict, mode: str | None, target_c: float | None, fan: str | None = None) -> bool:
    """Does a unit readback show what a command asked for? (late confirmation)"""
    want_mode = params.get("mode")
    if want_mode is not None and mode != want_mode:
        return False
    if want_mode != "off":
        if params.get("target_c") is not None and (target_c is None or abs(target_c - params["target_c"]) > 0.3):
            return False
        if params.get("fan") is not None and fan != params["fan"]:
            return False
    return True


def watchdog_needed(mode: str | None, target_c: float | None, temp: float | None, band: float) -> bool:
    """The unit runs to reach a target the room is clearly away from (heat: too cold, cool: too warm)."""
    if mode is None or target_c is None or temp is None:
        return False
    if mode in HEATING_MODES:
        return temp < target_c - band / 2
    if mode in COOLING_MODES:
        return temp > target_c + band / 2
    return False


def watchdog_progress(mode: str, start_temp: float, temp: float) -> float:
    """°C moved toward the target since the watchdog window started (negative = wrong way)."""
    return temp - start_temp if mode in HEATING_MODES else start_temp - temp


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
            "Airco niet bereikbaar; laatste instelling blijft actief. Gebruik zo nodig de afstandsbediening.",
            override_until=z.override_until,
            sensor_valid=valid,
        )

    observed = observed_state(z.unit)

    # 2. manual override & external changes (remote control) ----------------------------
    control = z.control
    override_until = z.override_until
    if control == "manual" and override_until and now >= override_until:
        control = "schedule"
        override_until = None
        log.append("Handmatige periode verlopen, terug naar schema.")

    baseline: dict | None = None
    mismatch = 0
    base = z.last_commanded or {}
    if z.pending:
        pass  # our own command is on its way: the unit may not show it yet
    elif not base.get("mode"):
        baseline = observed  # no baseline yet (new zone, after resume): adopt what the unit shows
    elif detect_external_change(z.unit, base):
        mismatch = z.mismatch_count + (1 if z.readback_new else 0)
        if mismatch < MISMATCH_READBACKS:
            # Seen once: wait for the next readback before concluding anything, and do not
            # undo a possible remote change in the meantime.
            return Decision(
                control,
                None,
                "checking_change",
                None,
                override_until=override_until,
                mismatch_count=mismatch,
                log=log,
                sensor_valid=valid,
            )
        baseline, mismatch = observed, 0
        if z.unit.mode == "off":
            # Deliberate off: never auto-reactivate (plan §5).
            log.append("Unit met afstandsbediening uitgezet; automaat blijft uit tot je hervat.")
            return Decision(
                "off",
                None,
                "external_off",
                "Uitgezet met afstandsbediening.",
                override_source="remote_control",
                log=log,
                sensor_valid=valid,
                baseline=baseline,
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
            baseline=baseline,
        )

    if control in ("manual", "off"):
        return Decision(
            control,
            None,
            "override_active" if control == "manual" else "zone_off",
            None if control == "manual" else "Uitgezet; hervat om het schema te volgen.",
            override_until=override_until if control == "manual" else None,
            log=log,
            sensor_valid=valid,
            baseline=baseline,
            mismatch_count=mismatch,
            mode=z.unit.mode,
            target_base_c=z.unit.target_c,
        )

    out_control = "schedule" if valid else "oem_fallback"
    common = {"log": log, "sensor_valid": valid, "baseline": baseline, "mismatch_count": mismatch}
    sensor_notice = notice if not valid else None

    if z.pending:
        return Decision(out_control, None, "pending", sensor_notice, **common)

    # 4. schedule -------------------------------------------------------------------------------
    entry = scheduled_entry(z.schedule, now, z.tz)
    if entry is None:
        return Decision(
            out_control, None, "no_schedule", "Geen schema ingesteld; de airco houdt zijn eigen instelling.", **common
        )
    unit_mode = z.unit.mode
    if entry.mode:
        mode = entry.mode
    elif unit_mode in (None, "off"):
        # A block without a mode keeps the current mode, and never starts a unit that is off.
        return Decision(out_control, None, "keep_off", sensor_notice, **common)
    else:
        mode = unit_mode
    base_target = entry.target_c
    common.update(mode=mode, target_base_c=base_target)

    if mode == "off":
        send = {"mode": "off"} if unit_mode not in (None, "off") else None
        return Decision(out_control, send, "schedule_off", sensor_notice, **common)

    # 1. safety: shared outdoor unit, capabilities
    for b in blocked_modes(z.caps, z.other_zone_modes):
        if b["mode"] == mode:
            return Decision(out_control, None, "mode_blocked", b["reason"], **common)
    if mode not in z.caps.modes:
        return Decision(
            out_control, None, "mode_unsupported", f"Modus '{mode}' wordt door deze unit niet ondersteund.", **common
        )

    # 3./5. setpoint: scheduled target + stored correction (+ adaptive), bounded
    correction = z.correction_c or 0.0
    new_correction = correction
    target: float | None = None
    if mode != "fan":
        if valid and mode in CORRECTED_MODES:
            assert z.sensor and z.sensor.value is not None
            err = base_target - z.sensor.value  # >0: room too cold, <0: room too warm (any mode)
            if abs(err) > effective_band(p) / 2:
                # Move at least one setpoint step toward the error, so rounding never swallows it.
                delta = max(z.caps.step_c, min(p["max_step_c"], abs(err)) * 0.5)
                new_correction = correction + math.copysign(delta, err)
            lo, hi = correction_limits(p, mode)
            new_correction = round(max(lo, min(hi, new_correction)), 2)
            offset = new_correction + z.adaptive_offset_c
            offset = max(-p["max_offset_c"], min(p["max_offset_c"], offset))
        else:
            new_correction = 0.0  # stale/invalid sensor or uncorrected mode: OEM regulates on its own sensor
            offset = 0.0
        target = max(z.caps.min_c, min(z.caps.max_c, base_target + offset))
        prev = z.unit.target_c if unit_mode == mode else None
        if prev is not None and abs(target - prev) > p["max_step_c"]:
            target = prev + (p["max_step_c"] if target > prev else -p["max_step_c"])
            log.append("Setpoint-sprong begrensd.")
        target = round_to_step(target, z.caps.step_c, prefer_up=offset >= 0)
        target = max(z.caps.min_c, min(z.caps.max_c, target))

    want: dict = {"mode": mode}
    if target is not None:
        want["target_c"] = target
    same_mode = unit_mode == mode
    same_target = target is None or (z.unit.target_c is not None and abs(z.unit.target_c - target) < z.caps.step_c / 2)
    if same_mode and same_target:
        return Decision(out_control, None, "no_change", sensor_notice, correction_c=new_correction, **common)

    # rate limits: a held-back change does not store its correction (no wind-up)
    if z.last_command_at and (now - z.last_command_at).total_seconds() < p["min_interval_s"] and same_mode:
        log.append("Geen wijziging: minimale tijd tussen wijzigingen nog niet verstreken.")
        return Decision(out_control, None, "min_interval", sensor_notice, **common)
    starting = unit_mode in (None, "off")
    if starting and _starts_last_hour(z.start_times, now) >= p["max_starts_per_hour"]:
        log.append("Geen start: maximum aantal starts per uur bereikt.")
        return Decision(out_control, None, "max_starts", "Maximum aantal starts per uur bereikt.", **common)

    return Decision(
        out_control,
        want,
        "schedule" if valid else "fallback_schedule",
        sensor_notice,
        correction_c=new_correction,
        starting=starting,
        **common,
    )
