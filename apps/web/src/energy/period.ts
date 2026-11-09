// Calendar periods for the energy page, always in the HOME's time zone (verbeterlijst 39).
// Dates are plain "YYYY-MM-DD" strings (a calendar day in the home's zone); arithmetic runs on UTC
// dates so the browser's own zone and DST never shift a day. Weeks start on Monday, like the API.
import type { Period } from '../api/types';
import { DAY_LABEL, DAY_SHORT } from '../i18n';

export const MONTH_LONG = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december'] as const;
export const MONTH_SHORT = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'] as const;

export const PERIOD_LABEL: Record<Period, string> = { day: 'Dag', week: 'Week', month: 'Maand', year: 'Jaar' };

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function isValidDay(s: string | null | undefined): s is string {
  const m = s ? DAY_RE.exec(s) : null;
  if (!m) return false;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
}

function toUtc(s: string): Date {
  const m = DAY_RE.exec(s);
  if (!m) throw new Error(`ongeldige datum: ${s}`);
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
}
function fromUtc(d: Date): string {
  return `${String(d.getUTCFullYear()).padStart(4, '0')}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
}

export interface ZonedParts {
  y: number;
  m: number; // 1..12
  d: number;
  h: number; // 0..23
  min: number;
  /** 0 = Monday … 6 = Sunday */
  wd: number;
}

/** Calendar parts of an instant in a time zone (falls back to the browser zone for an unknown zone). */
export function zonedParts(at: Date | string, tz?: string): ZonedParts {
  const date = typeof at === 'string' ? new Date(at) : at;
  let parts: Intl.DateTimeFormatPart[];
  const opts: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
  try {
    parts = new Intl.DateTimeFormat('en-GB', { ...opts, timeZone: tz }).formatToParts(date);
  } catch {
    parts = new Intl.DateTimeFormat('en-GB', opts).formatToParts(date);
  }
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const y = get('year');
  const m = get('month');
  const d = get('day');
  const wd = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  return { y, m, d, h: get('hour') % 24, min: get('minute'), wd };
}

/** Today's date in the home's time zone. */
export function todayIn(tz?: string, now: Date = new Date()): string {
  const p = zonedParts(now, tz);
  return `${p.y}-${String(p.m).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`;
}

/** The calendar day an instant falls on in the home's time zone. */
export function dayOf(at: Date | string, tz?: string): string {
  return todayIn(tz, typeof at === 'string' ? new Date(at) : at);
}

export function addDays(s: string, n: number): string {
  const d = toUtc(s);
  d.setUTCDate(d.getUTCDate() + n);
  return fromUtc(d);
}

export function addMonths(s: string, n: number): string {
  const d = toUtc(s);
  const day = d.getUTCDate();
  const first = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  first.setUTCDate(Math.min(day, last));
  return fromUtc(first);
}

/** First day of the period that contains `s`. */
export function periodStart(s: string, period: Period): string {
  const d = toUtc(s);
  switch (period) {
    case 'day':
      return s;
    case 'week':
      return addDays(s, -((d.getUTCDay() + 6) % 7));
    case 'month':
      return fromUtc(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1)));
    case 'year':
      return fromUtc(new Date(Date.UTC(d.getUTCFullYear(), 0, 1)));
  }
}

/** Last day (inclusive) of the period that contains `s`. */
export function periodLastDay(s: string, period: Period): string {
  return addDays(shiftPeriod(periodStart(s, period), period, 1), -1);
}

/** Same period type, `n` periods later (negative = earlier). Returns the new period's first day. */
export function shiftPeriod(s: string, period: Period, n: number): string {
  const start = periodStart(s, period);
  switch (period) {
    case 'day':
      return addDays(start, n);
    case 'week':
      return addDays(start, 7 * n);
    case 'month':
      return addMonths(start, n);
    case 'year':
      return addMonths(start, 12 * n);
  }
}

export function isCurrentPeriod(s: string, period: Period, today: string): boolean {
  return periodStart(s, period) === periodStart(today, period);
}

/** A later period exists only once it has started. */
export function canGoNext(s: string, period: Period, today: string): boolean {
  return shiftPeriod(s, period, 1) <= today;
}

/** Number of calendar days from a to b, both included. */
export function daysInclusive(a: string, b: string): number {
  return Math.round((toUtc(b).getTime() - toUtc(a).getTime()) / 86_400_000) + 1;
}

function parts(s: string) {
  const d = toUtc(s);
  return { y: d.getUTCFullYear(), m: d.getUTCMonth(), d: d.getUTCDate(), wd: (d.getUTCDay() + 6) % 7 };
}

/** Page title for a period, in Dutch: "zondag 8 november", "3–9 nov", "november 2026", "2026".
 * The year is added when it is not the current year. */
export function periodTitle(s: string, period: Period, today: string): string {
  const start = parts(periodStart(s, period));
  const thisYear = parts(today).y;
  const yr = (y: number) => (y !== thisYear ? ` ${y}` : '');
  switch (period) {
    case 'day':
      return `${DAY_LABEL[start.wd]} ${start.d} ${MONTH_LONG[start.m]}${yr(start.y)}`;
    case 'week': {
      const end = parts(periodLastDay(s, period));
      if (start.y !== end.y) return `${start.d} ${MONTH_SHORT[start.m]} ${start.y} – ${end.d} ${MONTH_SHORT[end.m]} ${end.y}`;
      if (start.m !== end.m) return `${start.d} ${MONTH_SHORT[start.m]} – ${end.d} ${MONTH_SHORT[end.m]}${yr(end.y)}`;
      return `${start.d}–${end.d} ${MONTH_SHORT[end.m]}${yr(end.y)}`;
    }
    case 'month':
      return `${MONTH_LONG[start.m]} ${start.y}`;
    case 'year':
      return String(start.y);
  }
}

/** "Vandaag", "Vorige week", … for the current and previous period, else null. */
export function relativeLabel(s: string, period: Period, today: string): string | null {
  const cur = periodStart(today, period);
  const start = periodStart(s, period);
  const prev = shiftPeriod(today, period, -1);
  const names: Record<Period, [string, string]> = {
    day: ['Vandaag', 'Gisteren'],
    week: ['Deze week', 'Vorige week'],
    month: ['Deze maand', 'Vorige maand'],
    year: ['Dit jaar', 'Vorig jaar'],
  };
  if (start === cur) return names[period][0];
  if (start === prev) return names[period][1];
  return null;
}

/** "8 nov 2026" style short date. */
export function shortDay(s: string): string {
  const p = parts(s);
  return `${p.d} ${MONTH_SHORT[p.m]} ${p.y}`;
}

/** Short axis label of a chart bucket (bucket start in the home's zone). */
export function bucketAxisLabel(startIso: string, period: Period, tz?: string): string {
  const p = zonedParts(startIso, tz);
  switch (period) {
    case 'day':
      return String(p.h).padStart(2, '0');
    case 'week':
      return `${DAY_SHORT[p.wd]} ${p.d}`;
    case 'month':
      return String(p.d);
    case 'year':
      return MONTH_SHORT[p.m - 1];
  }
}

/** Full label of a chart bucket for screen readers and tooltips. */
export function bucketLongLabel(startIso: string, endIso: string | undefined, period: Period, tz?: string): string {
  const p = zonedParts(startIso, tz);
  switch (period) {
    case 'day': {
      const e = endIso ? zonedParts(endIso, tz) : { h: (p.h + 1) % 24, min: 0 };
      const hm = (h: number, m: number) => `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
      return `${hm(p.h, p.min)}–${hm(e.h, e.min)}`;
    }
    case 'week':
      return `${DAY_LABEL[p.wd]} ${p.d} ${MONTH_LONG[p.m - 1]}`;
    case 'month':
      return `${DAY_LABEL[p.wd]} ${p.d} ${MONTH_LONG[p.m - 1]}`;
    case 'year':
      return `${MONTH_LONG[p.m - 1]} ${p.y}`;
  }
}
