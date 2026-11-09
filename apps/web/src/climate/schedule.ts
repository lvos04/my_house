// Week schedule logic for the schedule editor (verbeterlijst 12). Framework-free so it can be tested.
//
// The API stores blocks as {days[], start, target_c, mode|null, label}. The editor works per day: a
// "week" is 7 lists of blocks (0 = maandag … 6 = zondag, like Python's weekday()). Saving merges
// identical blocks on several days back into one API block.
//
// Block semantics (backend): with a mode the block runs that mode (it may start the unit); without a
// mode it keeps the mode the unit runs in and only sets the target, and never starts a unit that is
// off; mode "off" switches the unit off. No blocks at all: the regulation sends nothing.
import type { HvacMode, Schedule, ScheduleBlock } from '../api/types';
import { DAY_LABEL } from '../i18n';
import { MODE_LABEL, temp } from '../format';

export const MINUTES_PER_DAY = 1440;
const MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY;
/** API limits (routers/zones.py Block / Schedule). */
export const API_MIN_C = 10;
export const API_MAX_C = 30;
export const MAX_LABEL = 50;
export const MAX_BLOCKS = 100;

export const toMin = (hhmm: string): number => {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
};
export const fromMin = (min: number): string => {
  const m = ((min % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
export const isTime = (s: string): boolean => /^([01]\d|2[0-3]):[0-5]\d$/.test(s);

/** One block on one day, as edited. `target` is the text in the field (accepts a decimal comma). */
export interface EditBlock {
  id: number;
  start: string;
  target: string;
  mode: HvacMode | null;
  label: string;
}
export type Week = EditBlock[][];

let nextId = 1;
export const newBlockId = () => nextId++;

export function parseTarget(s: string): number {
  const t = s.trim().replace(',', '.');
  return t === '' ? NaN : Number(t);
}
/** Text for a number field: "21" / "18,5" (Dutch decimal comma). */
export function targetText(v: number): string {
  return Number.isInteger(v) ? String(v) : String(v).replace('.', ',');
}

export function emptyWeek(): Week {
  return Array.from({ length: 7 }, () => []);
}

export function sortDay(day: EditBlock[]): EditBlock[] {
  return [...day].sort((a, b) => (isTime(a.start) ? toMin(a.start) : 9999) - (isTime(b.start) ? toMin(b.start) : 9999));
}

/** API blocks → per-day lists (sorted by start). */
export function blocksToWeek(blocks: ScheduleBlock[]): Week {
  const week = emptyWeek();
  for (const b of blocks) {
    for (const d of b.days) {
      if (d < 0 || d > 6) continue;
      week[d].push({ id: newBlockId(), start: b.start, target: targetText(b.target_c), mode: b.mode ?? null, label: b.label ?? '' });
    }
  }
  return week.map(sortDay);
}

/** Per-day lists → API blocks. Identical blocks on several days become one block with several days.
 * Call validateWeek first: invalid targets are sent as NaN otherwise. */
export function weekToBlocks(week: Week): ScheduleBlock[] {
  const out: ScheduleBlock[] = [];
  const byKey = new Map<string, ScheduleBlock>();
  week.forEach((day, d) => {
    for (const b of sortDay(day)) {
      const target_c = parseTarget(b.target);
      const label = b.label.trim();
      const key = `${b.start}|${target_c}|${b.mode ?? ''}|${label}`;
      const found = byKey.get(key);
      if (found) {
        if (!found.days.includes(d)) found.days.push(d);
      } else {
        const blk: ScheduleBlock = { days: [d], start: b.start, target_c, mode: b.mode, label };
        byKey.set(key, blk);
        out.push(blk);
      }
    }
  });
  return out
    .map((b) => ({ ...b, days: [...b.days].sort((x, y) => x - y) }))
    .sort((a, b) => toMin(a.start) - toMin(b.start) || a.days[0] - b.days[0]);
}

/** Stable text form, to see whether the editor differs from what the server has. */
export function scheduleKey(blocks: ScheduleBlock[]): string {
  const norm = blocks
    .flatMap((b) => b.days.map((d) => `${d}|${b.start}|${b.target_c}|${b.mode ?? ''}|${(b.label ?? '').trim()}`))
    .sort();
  return norm.join(';');
}

/** Copy one day's blocks to other days (replacing what those days had). */
export function copyDay(week: Week, from: number, to: number[]): Week {
  return week.map((day, d) =>
    d !== from && to.includes(d) ? week[from].map((b) => ({ ...b, id: newBlockId() })) : day,
  );
}

/** A start time for a new block: one hour after the last block of the day, else 07:00. */
export function suggestStart(day: EditBlock[]): string {
  const times = day.filter((b) => isTime(b.start)).map((b) => toMin(b.start));
  if (!times.length) return '07:00';
  const taken = new Set(times);
  let m = Math.min(Math.max(...times) + 60, MINUTES_PER_DAY - 30);
  while (taken.has(m) && m > 0) m -= 30;
  return fromMin(m);
}

/* ---------- templates ---------- */

export type TemplateKind = 'constant' | 'nachtverlaging' | 'voorverwarmen';
export const TEMPLATE_LABEL: Record<TemplateKind, string> = {
  constant: 'Constant',
  nachtverlaging: 'Nachtverlaging',
  voorverwarmen: 'Voorverwarmen',
};
export const TEMPLATE_HELP: Record<TemplateKind, string> = {
  constant: 'De hele week dezelfde temperatuur.',
  nachtverlaging: 'Overdag de comforttemperatuur, van 22:30 tot 07:00 lager.',
  voorverwarmen:
    'Werkdagen: om 06:30 opwarmen, overdag (08:30–16:30) lager, vanaf 16:30 weer warm voor thuiskomst, ’s nachts lager. Weekend: 07:30–23:00 comfort.',
};
export interface TemplateOptions {
  comfort: number;
  low: number;
  mode: HvacMode | null;
}

export function templateWeek(kind: TemplateKind, o: TemplateOptions): Week {
  const b = (start: string, t: number, label: string): EditBlock => ({ id: newBlockId(), start, target: targetText(t), mode: o.mode, label });
  return Array.from({ length: 7 }, (_, d) => {
    if (kind === 'constant') return [b('00:00', o.comfort, 'Comfort')];
    if (kind === 'nachtverlaging') return [b('07:00', o.comfort, 'Dag'), b('22:30', o.low, 'Nacht')];
    if (d < 5) return [b('06:30', o.comfort, 'Opwarmen'), b('08:30', o.low, 'Weg'), b('16:30', o.comfort, 'Thuis'), b('22:30', o.low, 'Nacht')];
    return [b('07:30', o.comfort, 'Dag'), b('23:00', o.low, 'Nacht')];
  });
}

/* ---------- validation (mirrors the API, with Dutch messages) ---------- */

export interface Range {
  min_c: number;
  max_c: number;
  /** Modes the unit supports; empty = unknown (no unit linked, or a profile). */
  modes: HvacMode[];
}
export interface Issue {
  day: number;
  /** Index within the (sorted) day, or -1 for the whole schedule. */
  index: number;
  blockId: number | null;
  field: 'start' | 'target' | 'mode' | 'label' | 'schedule';
  message: string;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function validateWeek(week: Week, range?: Range | null): Issue[] {
  const issues: Issue[] = [];
  const lo = Math.max(API_MIN_C, range?.min_c ?? API_MIN_C);
  const hi = Math.min(API_MAX_C, range?.max_c ?? API_MAX_C);
  week.forEach((day, d) => {
    const seen = new Map<string, number>();
    day.forEach((b, i) => {
      const where = `${cap(DAY_LABEL[d])}, blok ${i + 1}`;
      const add = (field: Issue['field'], message: string) => issues.push({ day: d, index: i, blockId: b.id, field, message });
      if (!isTime(b.start)) add('start', `${where}: vul een begintijd in als UU:MM, bijvoorbeeld 07:00.`);
      else if (seen.has(b.start)) add('start', `Op ${DAY_LABEL[d]} beginnen twee blokken om ${b.start}. Kies een andere begintijd.`);
      else seen.set(b.start, i);
      const t = parseTarget(b.target);
      if (!Number.isFinite(t)) add('target', `${where}: vul een doeltemperatuur in.`);
      else if (t < lo || t > hi) {
        const unit = range && (range.min_c > API_MIN_C || range.max_c < API_MAX_C) ? ' (bereik van de airco)' : '';
        add('target', `${where}: ${temp(t)} ligt buiten ${temp(lo, 0)}–${temp(hi, 0)}${unit}.`);
      }
      if (b.mode && b.mode !== 'off' && range && range.modes.length && !range.modes.includes(b.mode))
        add('mode', `${where}: de airco kan niet ${(MODE_LABEL[b.mode] ?? b.mode).toLowerCase()}.`);
      if (b.label.trim().length > MAX_LABEL) add('label', `${where}: de naam is langer dan ${MAX_LABEL} tekens.`);
    });
  });
  if (!issues.length && weekToBlocks(week).length > MAX_BLOCKS)
    issues.push({ day: 0, index: -1, blockId: null, field: 'schedule', message: `Het schema heeft meer dan ${MAX_BLOCKS} verschillende blokken. Maak het eenvoudiger.` });
  return issues;
}

/* ---------- reading the schedule ---------- */

export interface Segment {
  from: number;
  to: number;
  target_c: number;
  label: string;
}

/** Weekday index used by the schedule API: 0 = maandag … 6 = zondag (Python weekday()). */
export function weekdayIndex(d: Date, tz?: string): number {
  const name = d.toLocaleDateString('en-US', { weekday: 'short', timeZone: tz });
  return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(name);
}

/** Day of week (0 = maandag) and minute of the day in the home's time zone. */
export function localDayMinute(now: Date, tz?: string): { day: number; minute: number } {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(now);
  const h = Number(parts.find((p) => p.type === 'hour')?.value ?? 0);
  const m = Number(parts.find((p) => p.type === 'minute')?.value ?? 0);
  return { day: weekdayIndex(now, tz), minute: (h % 24) * 60 + m };
}

/** Turn the weekly schedule into today's segments (minutes since midnight). */
export function todaySegments(s: Pick<Schedule, 'blocks'>, day: number): Segment[] {
  const prev = (day + 6) % 7;
  const today = s.blocks.filter((b) => b.days.includes(day)).sort((a, b) => toMin(a.start) - toMin(b.start));
  const carry = s.blocks.filter((b) => b.days.includes(prev)).sort((a, b) => toMin(a.start) - toMin(b.start)).at(-1);
  const segs: Segment[] = [];
  if (carry && (!today.length || toMin(today[0].start) > 0)) {
    segs.push({ from: 0, to: today.length ? toMin(today[0].start) : 1440, target_c: carry.target_c, label: carry.label });
  }
  today.forEach((b, i) => {
    segs.push({ from: toMin(b.start), to: i + 1 < today.length ? toMin(today[i + 1].start) : 1440, target_c: b.target_c, label: b.label });
  });
  return segs.filter((x) => x.to > x.from);
}

export interface WeekSegment {
  from: number;
  to: number;
  block: EditBlock;
  /** Continues from an earlier day. */
  carried: boolean;
}

/** The block still running at the start of `day` (the last block before it, wrapping over the week). */
function carryInto(week: Week, day: number): EditBlock | null {
  for (let back = 1; back <= 7; back++) {
    const d = (day - back + 7) % 7;
    const valid = sortDay(week[d]).filter((b) => isTime(b.start));
    if (valid.length) return valid[valid.length - 1];
  }
  return null;
}

/** Segments of one day of the edited week, including the block carried over from before. */
export function daySegments(week: Week, day: number): WeekSegment[] {
  const today = sortDay(week[day]).filter((b) => isTime(b.start));
  const segs: WeekSegment[] = [];
  const carry = carryInto(week, day);
  if (carry && (!today.length || toMin(today[0].start) > 0)) {
    segs.push({ from: 0, to: today.length ? toMin(today[0].start) : MINUTES_PER_DAY, block: carry, carried: true });
  }
  today.forEach((b, i) => {
    segs.push({ from: toMin(b.start), to: i + 1 < today.length ? toMin(today[i + 1].start) : MINUTES_PER_DAY, block: b, carried: false });
  });
  return segs.filter((s) => s.to > s.from);
}

export interface Change {
  /** Minutes from now. */
  inMinutes: number;
  day: number;
  block: EditBlock;
}

/** The next block start after `now` (like climate.next_change on the server), or null without blocks. */
export function nextChange(week: Week, now: Date, tz?: string): Change | null {
  const { day, minute } = localDayMinute(now, tz);
  const mow = day * MINUTES_PER_DAY + minute;
  let best: Change | null = null;
  week.forEach((blocks, d) => {
    for (const b of blocks) {
      if (!isTime(b.start)) continue;
      const start = d * MINUTES_PER_DAY + toMin(b.start);
      const ahead = (start - mow + MINUTES_PER_WEEK) % MINUTES_PER_WEEK || MINUTES_PER_WEEK;
      if (!best || ahead < best.inMinutes) best = { inMinutes: ahead, day: d, block: b };
    }
  });
  return best;
}

/** The block active at `now`, wrapping over the week. */
export function activeBlock(week: Week, now: Date, tz?: string): EditBlock | null {
  const { day, minute } = localDayMinute(now, tz);
  const mow = day * MINUTES_PER_DAY + minute;
  let best: { ago: number; b: EditBlock } | null = null;
  week.forEach((blocks, d) => {
    for (const b of blocks) {
      if (!isTime(b.start)) continue;
      const ago = (mow - (d * MINUTES_PER_DAY + toMin(b.start)) + MINUTES_PER_WEEK) % MINUTES_PER_WEEK;
      if (!best || ago < best.ago) best = { ago, b };
    }
  });
  return best ? (best as { b: EditBlock }).b : null;
}

/** "vandaag" / "morgen" / "woensdag" for a change `inMinutes` from now on weekday `day`. */
export function whenText(c: Change, now: Date, tz?: string): string {
  const { day, minute } = localDayMinute(now, tz);
  const daysAhead = Math.floor((minute + c.inMinutes) / MINUTES_PER_DAY);
  const at = c.block.start;
  if (daysAhead === 0) return `vandaag om ${at}`;
  if (daysAhead === 1) return `morgen om ${at}`;
  if (daysAhead >= 7 && c.day === day) return `volgende week ${DAY_LABEL[c.day]} om ${at}`;
  return `${DAY_LABEL[c.day]} om ${at}`;
}

/** What a block does with the mode, in plain Dutch. */
export function modeEffect(mode: HvacMode | null | undefined): string {
  if (!mode) return 'houdt de huidige modus';
  if (mode === 'off') return 'zet de airco uit';
  return `zet de airco op ${(MODE_LABEL[mode] ?? mode).toLowerCase()}`;
}

/** One line for a block: "Nacht · 18,0° · houdt de huidige modus". */
export function blockSummary(b: Pick<EditBlock, 'label' | 'target' | 'mode'>): string {
  const t = parseTarget(b.target);
  if (b.mode === 'off') return [b.label.trim(), 'uit'].filter(Boolean).join(' · ');
  return [b.label.trim(), Number.isFinite(t) ? temp(t) : null, modeEffect(b.mode)].filter(Boolean).join(' · ');
}
