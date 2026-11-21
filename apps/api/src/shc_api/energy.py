"""Energy accounting from cumulative counters (plan §6). Pure functions, no database.

- Consumption = sum of *plausible* steps between consecutive counter readings, in one pass.
- Plausibility (verbeterlijst 20): a step may not exceed the maximum power of the meter's role
  (P1 ~17 kW, Energy Socket 3.7 kW, airco meter configurable). A step that does is "suspect" and
  is not counted. A drop (counter reset / meter replaced) or an implausible jump is only accepted
  as a new base when the following readings rise consistently from it; otherwise the odd reading
  is an outlier and is skipped. Negative counter values are never accepted.
- Durations and overlaps are computed on UTC instants (POSIX timestamps), never on wall-clock
  time, so DST transition days have 23 or 25 complete hours (verbeterlijst 21).
- Buckets are local calendar periods; a delta is assigned to buckets by time share.
- A bucket is incomplete when it is not fully covered or contains a gap longer than `max_gap`.
- Tariffs (verbeterlijst 35) are looked up per local day; T1/T2 prices are applied with the
  measured T1/T2 split when the meter reports it.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

RESET_SMALL_KWH = 5.0  # legacy constant (kept for callers); resets are now confirmed by later readings

# Cumulative counters the worker aggregates per hour. Electricity in kWh, gas in m³.
ELECTRICITY_COUNTERS = (
    "energy_import_kwh",
    "energy_export_kwh",
    "energy_import_t1_kwh",
    "energy_import_t2_kwh",
    "energy_export_t1_kwh",
    "energy_export_t2_kwh",
)
GAS_METRIC = "gas_m3"
COUNTER_METRICS = (*ELECTRICITY_COUNTERS, GAS_METRIC)
POWER_METRICS = ("power_w",)

# Maximum plausible power (kW, or m³/h for gas) per role and per device kind. A counter step that
# implies more than this is suspect. Overridable per role (Setting "energy_max_kw") and per device
# (Device.calibration["max_kw"]).
ROLE_MAX_KW = {"grid": 17.25, "boiler": 3.7, "airco": 7.4}
KIND_MAX_KW = {"p1_meter": 17.25, "energy_socket": 3.7, "kwh_meter": 11.0}
DEFAULT_MAX_KW = 25.0
GAS_MAX_M3_PER_H = 10.0

NEG_TOLERANCE = 0.01  # counter jitter (kWh / m³) that is treated as "no change", not as a reset
CONFIRM_READINGS = 2  # readings that must rise plausibly from a new base before a reset is accepted
DEFAULT_MAX_GAP = timedelta(minutes=20)
GAS_MAX_GAP = timedelta(hours=2)  # DSMR4 gas meters only update once per hour

# Flags stored in energy_hourly.flags and merged into buckets.
F_GAP = "gap"  # an interval longer than max_gap (missing readings, delta spread over the gap)
F_INCOMPLETE = "incomplete"  # hour not fully covered
F_SUSPECT = "suspect"  # an implausible reading or step was skipped
F_RESET = "reset"  # counter reset / meter replacement accepted
F_RESET_SKIPPED = "reset_skipped"
F_ESTIMATED = "estimated"  # derived value had to be clamped or a part is missing

NOTE_GAP = "ontbrekende metingen"
NOTE_RESET = "tellerreset gedetecteerd"
NOTE_RESET_SKIPPED = "tellerreset; interval overgeslagen"
NOTE_SUSPECT = "verdachte meterstand overgeslagen"
NOTE_ESTIMATED = "deels geschat"
FLAG_NOTES = {
    F_GAP: NOTE_GAP, F_INCOMPLETE: NOTE_GAP, F_SUSPECT: NOTE_SUSPECT,
    F_RESET: NOTE_RESET, F_RESET_SKIPPED: NOTE_RESET_SKIPPED,
}


def max_gap_for(metric: str) -> timedelta:
    return GAS_MAX_GAP if metric == GAS_METRIC else DEFAULT_MAX_GAP


def max_rate_for(metric: str, role: str | None, kind: str | None, overrides: dict | None = None) -> float:
    """Maximum plausible rate (kW or m³/h) for a counter of this device."""
    if metric == GAS_METRIC:
        return float((overrides or {}).get("gas", GAS_MAX_M3_PER_H))
    o = overrides or {}
    if role and role in o:
        return float(o[role])
    if role and role in ROLE_MAX_KW:
        return ROLE_MAX_KW[role]
    if kind and kind in KIND_MAX_KW:
        return KIND_MAX_KW[kind]
    return float(o.get("default", DEFAULT_MAX_KW))


def weakest_quality(*qualities: str | None) -> str:
    if any(q not in ("measured", "estimated") for q in qualities):
        return "missing"
    return "estimated" if "estimated" in qualities else "measured"


class _Reading(NamedTuple):
    time: float
    value: float
    quality: str


@dataclass(slots=True)
class CounterPoint:
    at: datetime  # tz-aware UTC
    kwh: float  # counter value (kWh, or m³ for gas)
    quality: str = "measured"


@dataclass(slots=True)
class Interval:
    """A cleaned counter step: `amount` consumed between `t0` and `t1` (POSIX seconds)."""

    t0: float
    t1: float
    amount: float
    gap: bool = False
    flags: tuple[str, ...] = ()
    note: str | None = None
    quality: str = "missing"


@dataclass(slots=True)
class Bucket:
    start: datetime  # tz-aware (local for display); arithmetic always via timestamps
    end: datetime
    kwh: float = 0.0
    covered_s: float = 0.0
    complete: bool = True
    notes: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    point_quality: str = "measured"  # neutraal tot de eerste bijdrage

    @property
    def quality(self) -> str:
        if self.covered_s <= 0 or self.point_quality == "missing" or F_RESET_SKIPPED in self.flags:
            return "missing"
        if not self.complete or F_SUSPECT in self.flags or F_ESTIMATED in self.flags:
            return "estimated"
        return self.point_quality

    @property
    def start_ts(self) -> float:
        return self.start.timestamp()

    @property
    def end_ts(self) -> float:
        return self.end.timestamp()

    @property
    def duration_s(self) -> float:
        """Real elapsed seconds (UTC), so a DST day is 23 or 25 hours."""
        return self.end.timestamp() - self.start.timestamp()

    def add_flag(self, flag: str) -> None:
        self.flags.add(flag)
        note = FLAG_NOTES.get(flag)
        if note and note not in self.notes:
            self.notes.append(note)

    def finalize(self) -> None:
        self.complete = (
            self.covered_s + 60 >= self.duration_s
            and not self.flags.intersection({F_GAP, F_INCOMPLETE, F_RESET_SKIPPED})
            and self.point_quality != "missing"
        )


# --- periods and buckets ---------------------------------------------------------------


def period_bounds(period: str, day: date, tz: ZoneInfo) -> tuple[datetime, datetime, str]:
    """Return (start, end, bucket_unit) in local time for day/week/month/year containing `day`."""
    if period == "day":
        s = datetime.combine(day, time(0), tz)
        return s, datetime.combine(day + timedelta(days=1), time(0), tz), "hour"
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return (
            datetime.combine(monday, time(0), tz),
            datetime.combine(monday + timedelta(days=7), time(0), tz),
            "day",
        )
    if period == "month":
        first = day.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return datetime.combine(first, time(0), tz), datetime.combine(nxt, time(0), tz), "day"
    if period == "year":
        return (
            datetime.combine(date(day.year, 1, 1), time(0), tz),
            datetime.combine(date(day.year + 1, 1, 1), time(0), tz),
            "month",
        )
    raise ValueError(period)


def make_buckets(start: datetime, end: datetime, unit: str, tz: ZoneInfo) -> list[Bucket]:
    out: list[Bucket] = []
    cur = start
    while cur < end:
        if unit == "hour":
            # Step in UTC so the DST-repeated hour is its own bucket (23 or 25 buckets per day).
            nxt = (cur.astimezone(UTC) + timedelta(hours=1)).astimezone(tz)
        elif unit == "day":
            nxt = datetime.combine(cur.date() + timedelta(days=1), time(0), tz)
        else:
            first = cur.date().replace(day=1)
            nxt = datetime.combine((first + timedelta(days=32)).replace(day=1), time(0), tz)
        out.append(Bucket(cur, min(nxt, end)))
        cur = nxt
    return out


def floor_hour(t: datetime) -> datetime:
    t = t.astimezone(UTC)
    return t.replace(minute=0, second=0, microsecond=0)


# --- cleaning: one pass over the readings -------------------------------------------------


def _limit(rate: float | None, dt_s: float) -> float:
    if rate is None:
        return math.inf
    return rate * dt_s / 3600.0 * 1.1 + 0.02


def clean_intervals(
    points: Iterable[CounterPoint],
    max_rate: float | None = None,
    max_gap: timedelta = DEFAULT_MAX_GAP,
    confirm: int = CONFIRM_READINGS,
) -> list[Interval]:
    """Turn raw counter readings into plausible consumption intervals (single pass).

    `max_rate` is in kWh per hour (= kW) or m³/h; None means no upper limit.
    """
    pts: list[_Reading] = []
    for p in points:
        v = p.kwh
        if v is None or not math.isfinite(v):
            continue
        pts.append(_Reading(p.at.timestamp(), float(v), weakest_quality(p.quality)))
    pts.sort()
    gap_s = max_gap.total_seconds()
    out: list[Interval] = []

    def emit(a: _Reading, b: _Reading, amount: float, flags=(), note=None, quality: str | None = None) -> None:
        if b.time > a.time:
            interval_quality = quality or weakest_quality(a.quality, b.quality)
            out.append(Interval(a.time, b.time, amount, b.time - a.time > gap_s, flags, note, interval_quality))

    def plausible(a: _Reading, b: _Reading) -> bool:
        return -NEG_TOLERANCE <= b.value - a.value <= _limit(max_rate, b.time - a.time)

    def advance(a: _Reading, b: _Reading) -> _Reading:
        # Bij kleine negatieve jitter blijft de oude waarde bijdragen aan de volgende stap.
        if b.value < a.value:
            return _Reading(b.time, a.value, weakest_quality(a.quality, b.quality))
        return b

    base: _Reading | None = None
    pending: list[_Reading] = []
    last_t: float | None = None
    for p in pts:
        if p.time == last_t:
            continue
        last_t = p.time
        if p.value < 0:
            continue
        if base is None:
            base = p
        elif plausible(base, p):
            flags = (F_SUSPECT,) if pending else ()
            emit(base, p, max(0.0, p.value - base.value), flags, NOTE_SUSPECT if pending else None)
            base = advance(base, p)
            pending = []
        elif pending and plausible(pending[-1], p):
            pending.append(p)
            if len(pending) > confirm:
                base = _accept_new_base(base, pending, max_rate, emit)
                pending = []
        else:
            pending = [p]
    if base is not None and pending:
        out.append(Interval(base.time, pending[-1].time, 0.0, True, (F_SUSPECT, F_RESET_SKIPPED), NOTE_RESET_SKIPPED))
    return out


def _accept_new_base(base: _Reading, chain: list[_Reading], max_rate, emit) -> _Reading:
    c0 = chain[0]
    quality = weakest_quality(base.quality, *(p.quality for p in chain))
    if c0.value < base.value:
        if c0.value <= _limit(max_rate, c0.time - base.time) and (max_rate is not None or c0.value < RESET_SMALL_KWH):
            emit(base, c0, c0.value, (F_RESET,), NOTE_RESET, quality)
        else:
            emit(base, c0, 0.0, (F_RESET, F_RESET_SKIPPED), NOTE_RESET_SKIPPED, "missing")
    else:
        emit(base, c0, 0.0, (F_SUSPECT,), NOTE_SUSPECT, quality)
    for a, b in zip(chain, chain[1:], strict=False):
        emit(a, b, max(0.0, b.value - a.value))
    return chain[-1]


def deltas(
    points: list[CounterPoint], max_rate: float | None = None
) -> list[tuple[datetime, datetime, float, str | None]]:
    """(from, to, kwh, note) per interval; handles resets, outliers and duplicates."""
    return [
        (datetime.fromtimestamp(i.t0, UTC), datetime.fromtimestamp(i.t1, UTC), i.amount, i.note)
        for i in clean_intervals(points, max_rate)
    ]


# --- distribution: one pass over intervals and buckets -------------------------------------


def distribute(
    intervals: Sequence[Interval], buckets: Sequence[Bucket], lo: float | None = None, hi: float | None = None
) -> None:
    """Add interval amounts to buckets by time share (two-pointer, O(n + m)).

    Only the part of each interval inside [lo, hi) (POSIX seconds) is counted; this lets callers
    combine pre-aggregated hours with a raw tail without double counting.
    """
    if not buckets:
        return
    bs = [(b.start_ts, b.end_ts) for b in buckets]
    j = 0
    n = len(buckets)
    for iv in intervals:
        span = iv.t1 - iv.t0
        if span <= 0:
            continue
        s0 = iv.t0 if lo is None else max(iv.t0, lo)
        s1 = iv.t1 if hi is None else min(iv.t1, hi)
        if s1 <= s0:
            continue
        while j < n and bs[j][1] <= s0:
            j += 1
        k = j
        while k < n and bs[k][0] < s1:
            s = max(s0, bs[k][0])
            e = min(s1, bs[k][1])
            if e > s:
                b = buckets[k]
                b.kwh += iv.amount * (e - s) / span
                b.covered_s += e - s
                b.point_quality = weakest_quality(b.point_quality, iv.quality)
                if iv.gap:
                    b.add_flag(F_GAP)
                for f in iv.flags:
                    b.add_flag(f)
                if iv.note and iv.note not in b.notes:
                    b.notes.append(iv.note)
            k += 1


def aggregate(points: list[CounterPoint], buckets: list[Bucket], max_gap: timedelta = DEFAULT_MAX_GAP) -> None:
    """Distribute cleaned counter deltas over buckets proportionally to time overlap."""
    distribute(clean_intervals(points, None, max_gap), buckets)
    for b in buckets:
        b.finalize()


# --- hourly aggregation (worker) ------------------------------------------------------------


@dataclass(slots=True)
class HourAgg:
    hour_start: datetime  # UTC
    kwh: float
    first_value: float | None
    last_value: float | None
    samples: int
    flags: list[str]
    point_quality: str


def hourly(
    points: Sequence[CounterPoint],
    start: datetime,
    end: datetime,
    max_rate: float | None,
    max_gap: timedelta = DEFAULT_MAX_GAP,
) -> list[HourAgg]:
    """Hourly totals for UTC hours in [start, end). `points` may extend beyond the range
    (the last reading before and the first after are needed for the boundary intervals)."""
    start, end = floor_hour(start), floor_hour(end)
    hours: list[Bucket] = []
    cur = start
    while cur < end:
        hours.append(Bucket(cur, cur + timedelta(hours=1)))
        cur += timedelta(hours=1)
    if not hours:
        return []
    distribute(clean_intervals(points, max_rate, max_gap), hours)
    first: dict[int, float] = {}
    last: dict[int, float] = {}
    samples: dict[int, int] = {}
    s0 = start.timestamp()
    for p in points:
        if p.kwh is None or not math.isfinite(p.kwh):
            continue
        idx = int((p.at.timestamp() - s0) // 3600)
        if 0 <= idx < len(hours):
            samples[idx] = samples.get(idx, 0) + 1
            first.setdefault(idx, p.kwh)
            last[idx] = p.kwh
    out = []
    for i, h in enumerate(hours):
        if h.covered_s <= 0 and i not in samples:
            continue
        flags = set(h.flags)
        if h.covered_s + 60 < 3600:
            flags.add(F_INCOMPLETE)
        quality = h.point_quality if h.covered_s else "missing"
        out.append(HourAgg(h.start, h.kwh, first.get(i), last.get(i), samples.get(i, 0), sorted(flags), quality))
    return out


# --- tariffs ------------------------------------------------------------------------------


@dataclass(slots=True)
class TariffPeriod:
    valid_from: date
    import_per_kwh: float
    export_per_kwh: float | None = None
    fixed_per_day: float = 0.0
    import_t1_per_kwh: float | None = None
    import_t2_per_kwh: float | None = None
    gas_per_m3: float | None = None
    id: object = None


class TariffTable:
    """All tariffs of a home, loaded once; lookup by local date (binary search)."""

    def __init__(self, periods: Iterable[TariffPeriod]):
        self.periods = sorted(periods, key=lambda t: t.valid_from)
        self._keys = [t.valid_from for t in self.periods]

    def at(self, d: date) -> TariffPeriod | None:
        i = bisect.bisect_right(self._keys, d) - 1
        return self.periods[i] if i >= 0 else None

    def __bool__(self) -> bool:
        return bool(self.periods)


def import_price(t: TariffPeriod, t1_kwh: float = 0.0, t2_kwh: float = 0.0) -> float:
    """Effective import price: the T1/T2 prices weighted by the measured T1/T2 split, when both are known."""
    if t.import_t1_per_kwh is not None and t.import_t2_per_kwh is not None and (t1_kwh + t2_kwh) > 0:
        return (t1_kwh * t.import_t1_per_kwh + t2_kwh * t.import_t2_per_kwh) / (t1_kwh + t2_kwh)
    return t.import_per_kwh


def cost(kwh: float, price_per_kwh: float | None) -> float | None:
    return None if price_per_kwh is None else round(kwh * price_per_kwh, 2)
