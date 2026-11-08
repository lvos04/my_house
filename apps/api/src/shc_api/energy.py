"""Energy accounting from cumulative kWh counters (plan §6).

- Consumption = sum of positive deltas between consecutive counter readings.
- Counter reset (meter replaced, firmware reset, overflow): a drop is not negative usage;
  the new value counts from zero when it is small, otherwise the interval is skipped and flagged.
- Buckets are local calendar periods (DST aware via zoneinfo), delta assigned by time share.
- A bucket is incomplete when it has gaps longer than `max_gap`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

RESET_SMALL_KWH = 5.0  # after a reset, a first value below this is counted as consumption


@dataclass(slots=True)
class CounterPoint:
    at: datetime  # tz-aware UTC
    kwh: float


@dataclass(slots=True)
class Bucket:
    start: datetime  # local, tz-aware
    end: datetime
    kwh: float = 0.0
    covered_s: float = 0.0
    complete: bool = True
    notes: list[str] = field(default_factory=list)


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
            nxt = (cur.astimezone(ZoneInfo("UTC")) + timedelta(hours=1)).astimezone(tz)
        elif unit == "day":
            nxt = datetime.combine(cur.date() + timedelta(days=1), time(0), tz)
        else:
            first = cur.date().replace(day=1)
            nxt = datetime.combine((first + timedelta(days=32)).replace(day=1), time(0), tz)
        out.append(Bucket(cur, min(nxt, end)))
        cur = nxt
    return out


def deltas(points: list[CounterPoint]) -> list[tuple[datetime, datetime, float, str | None]]:
    """(from, to, kwh, note) per interval; handles resets and duplicates."""
    pts = sorted({(p.at, p.kwh) for p in points if p.kwh is not None})
    out = []
    for (t0, v0), (t1, v1) in zip(pts, pts[1:], strict=False):
        if t1 == t0:
            continue
        d = v1 - v0
        note = None
        if d < 0:
            if v1 < RESET_SMALL_KWH:
                d, note = v1, "tellerreset gedetecteerd"
            else:
                d, note = 0.0, "tellerreset; interval overgeslagen"
        out.append((t0, t1, d, note))
    return out


def aggregate(points: list[CounterPoint], buckets: list[Bucket], max_gap: timedelta = timedelta(minutes=20)) -> None:
    """Distribute counter deltas over buckets proportionally to time overlap."""
    for t0, t1, kwh, note in deltas(points):
        span = (t1 - t0).total_seconds()
        gap = (t1 - t0) > max_gap
        for b in buckets:
            s = max(t0, b.start)
            e = min(t1, b.end)
            if e <= s:
                continue
            share = (e - s).total_seconds() / span
            b.kwh += kwh * share
            b.covered_s += (e - s).total_seconds()
            if gap:
                b.complete = False
                if "ontbrekende metingen" not in b.notes:
                    b.notes.append("ontbrekende metingen")
            if note and note not in b.notes:
                b.notes.append(note)
    for b in buckets:
        if b.covered_s + 60 < (b.end - b.start).total_seconds():
            b.complete = False


def cost(kwh: float, price_per_kwh: float | None) -> float | None:
    return None if price_per_kwh is None else round(kwh * price_per_kwh, 2)
