"""Adaptive climate model and experiments (plan §5 'Adaptief', §6). Pure functions.

Model per zone, fitted on passive baseline data (15-minute samples while the unit runs):
    dT/dt [°C/h] = k * (setpoint - T) + c
k = how quickly the room follows the setpoint, c = drift (losses / gains).
From k we derive a bounded setpoint offset that helps reach the target within the band.
The model never acts by itself: the engine only uses it when the zone is opted in and a
version is active, and the offset is clamped (MAX_ADAPTIVE_OFFSET) under the hard policy.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MIN_SAMPLES = 96  # ~24 h of running time in 15-minute steps
# Days of raw readings the model is fitted on (temperature, target_c). The sensor retention never
# keeps less than this (worker/aggregates.py, RETENTION_MIN["sensor_days"]).
BASELINE_DAYS = 14
MIN_R2 = 0.3
MAX_ADAPTIVE_OFFSET = 0.5


@dataclass(slots=True)
class Sample:
    at: datetime
    temp: float
    setpoint: float
    running: bool


def resample(
    temps: list[tuple[datetime, float]],
    setpoints: list[tuple[datetime, float]],
    modes: list[tuple[datetime, str]],
    step: timedelta = timedelta(minutes=15),
) -> list[Sample]:
    """Align series on a fixed grid using last-known values (no interpolation into gaps > 2 steps)."""
    if not temps or not setpoints:
        return []
    start, end = temps[0][0], temps[-1][0]
    out: list[Sample] = []
    it = jt = mt = 0
    t = start
    last_t = last_s = None
    last_mode = "off"
    while t <= end:
        while it < len(temps) and temps[it][0] <= t:
            last_t = temps[it]
            it += 1
        while jt < len(setpoints) and setpoints[jt][0] <= t:
            last_s = setpoints[jt]
            jt += 1
        while mt < len(modes) and modes[mt][0] <= t:
            last_mode = modes[mt][1]
            mt += 1
        if last_t and last_s and t - last_t[0] <= 2 * step:
            out.append(Sample(t, last_t[1], last_s[1], last_mode not in ("off", None)))
        t += step
    return out


def fit(samples: list[Sample]) -> dict | None:
    """Least squares fit of dT/dt = k*(sp - T) + c on consecutive running samples."""
    xs, ys = [], []
    for a, b in zip(samples, samples[1:], strict=False):
        dt_h = (b.at - a.at).total_seconds() / 3600
        if not (a.running and b.running) or dt_h <= 0 or dt_h > 0.6:
            continue
        xs.append(a.setpoint - a.temp)
        ys.append((b.temp - a.temp) / dt_h)
    n = len(xs)
    if n < MIN_SAMPLES:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-6:
        return None
    k = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    c = my - k * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (k * x + c)) ** 2 for x, y in zip(xs, ys, strict=True))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "k_per_h": round(k, 4),
        "c_per_h": round(c, 4),
        "n": n,
        "r2": round(r2, 3),
        "usable": k > 0.05 and r2 >= MIN_R2,
    }


def advised_offset(model: dict, target: float, temp: float, band: float) -> float:
    """Extra setpoint offset so the room reaches target within ~1 h, bounded."""
    if not model.get("usable"):
        return 0.0
    err = target - temp
    if abs(err) <= band / 2:
        return 0.0
    k, c = model["k_per_h"], model["c_per_h"]
    # setpoint needed so that dT/dt ≈ err per hour: k*(sp-T)+c = err  ->  sp = T + (err-c)/k
    needed = temp + (err - c) / k
    off = needed - target
    return max(-MAX_ADAPTIVE_OFFSET, min(MAX_ADAPTIVE_OFFSET, round(off, 2)))


# --- experiments ----------------------------------------------------------------


@dataclass(slots=True)
class PeriodStats:
    variant: str
    days: float
    kwh: float | None
    mean_abs_dev_c: float | None
    starts: int
    outdoor_mean_c: float | None
    comfort_pct: float | None = None  # share of time within the comfort band
    other_zone_mean_target_c: float | None = None  # confounder: the other zone on the same outdoor unit
    notes: list[str] = field(default_factory=list)


def comfort_share(
    temps: list[tuple[datetime, float]],
    target_at: Callable[[datetime], float | None],
    band_c: float,
    end: datetime,
    max_gap: timedelta = timedelta(minutes=30),
) -> float | None:
    """Comfort score: % of (time-weighted) measured time the room was within ±band/2 of its target.

    Each sample counts until the next one, at most ``max_gap`` (longer gaps are not guessed).
    Moments without a target (unit scheduled off) are left out.
    """
    total = inside = 0.0
    for i, (at, value) in enumerate(temps):
        nxt = temps[i + 1][0] if i + 1 < len(temps) else end
        dur = min((nxt - at).total_seconds(), max_gap.total_seconds())
        if dur <= 0:
            continue
        target = target_at(at)
        if target is None:
            continue
        total += dur
        if abs(value - target) <= band_c / 2 + 1e-9:
            inside += dur
    return round(100 * inside / total, 1) if total else None


def compare(
    stats: list[PeriodStats],
    min_days: float = 5.0,
    max_outdoor_diff: float = 2.0,
    max_other_zone_diff: float = 1.0,
) -> dict:
    """Honest comparison: 'besparing' only with enough comparable data, else 'indicatief'."""
    reasons = []
    if len(stats) < 2:
        return {"verdict": "onvoldoende", "reasons": ["Minstens twee varianten nodig."], "rows": []}
    if any(s.days < min_days for s in stats):
        reasons.append(f"Minder dan {min_days:g} dagen per variant.")
    if any(s.kwh is None for s in stats):
        reasons.append("Geen kWh-meting voor de airco in elke periode.")
    outs = [s.outdoor_mean_c for s in stats]
    if any(o is None for o in outs):
        reasons.append("Geen buitentemperatuur gemeten; weersinvloed onbekend.")
    elif max(outs) - min(outs) > max_outdoor_diff:  # type: ignore[type-var]
        reasons.append("Buitentemperatuur verschilde te veel tussen de perioden.")
    others = [s.other_zone_mean_target_c for s in stats if s.other_zone_mean_target_c is not None]
    if len(others) == len(stats) and max(others) - min(others) > max_other_zone_diff:
        reasons.append(
            "De andere ruimte op dezelfde buitenunit werd per periode anders ingesteld; dat telt mee in het verbruik."
        )
    rows = []
    base = stats[0]
    for s in stats:
        per_day = s.kwh / s.days if s.kwh is not None and s.days else None
        base_pd = base.kwh / base.days if base.kwh is not None and base.days else None
        delta = (per_day - base_pd) / base_pd * 100 if per_day is not None and base_pd else None
        rows.append(
            {
                "variant": s.variant,
                "days": round(s.days, 1),
                "kwh_per_day": round(per_day, 2) if per_day is not None else None,
                "vs_first_pct": round(delta, 1) if delta is not None and not math.isnan(delta) else None,
                "mean_abs_dev_c": s.mean_abs_dev_c,
                "starts": s.starts,
                "outdoor_mean_c": s.outdoor_mean_c,
                "comfort_pct": s.comfort_pct,
                "other_zone_mean_target_c": s.other_zone_mean_target_c,
                "notes": list(s.notes),
            }
        )
    return {"verdict": "vergelijkbaar" if not reasons else "indicatief", "reasons": reasons, "rows": rows}
