// De grafiek "Laatste 24 uur" per uur: dezelfde metingen als de lijn, per klokuur samengevat voor de tabel (de
// WCAG-tweeling, Peil §10) en voor de bediening met het toetsenbord (pijltjes per uur). Alleen weergave: de API en
// de metingen zelf veranderen niet. Een uur zonder meting heet "Geen meting" en is nooit 0 (Peil §10, eerlijkheid);
// de lijn breekt op precies die uren, zodat lijn, tabel en voorleestekst hetzelfde zeggen.
import type { ZoneHistory } from '../api/types';
import { temp } from '../format';

export const HOUR_MS = 3_600_000;

/** Waar geen meting is, staat dit woord (grafiek, tabel en voorleestekst). */
export const GEEN_METING = 'Geen meting';

export interface MeasuredStats {
  /** Gemiddelde van de metingen in dit uur. */
  avg: number;
  min: number;
  max: number;
  /** Aantal metingen met een waarde. */
  n: number;
}

export interface HistorySlot {
  /** Begin en eind van het klokuur (ms sinds epoch). */
  start: number;
  end: number;
  /** Het deel van het uur waar de grafiek over gaat (het eerste en laatste uur zijn vaak korter). */
  from: number;
  to: number;
  measured: MeasuredStats | null;
  /** De doeltemperaturen in dit uur in volgorde, opeenvolgend gelijke waarden samengevoegd ([21] of [21, 18]). */
  target: number[] | null;
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const ms = (ts: string) => new Date(ts).getTime();

/** Kleinste en grootste eindige getal, of null zonder getallen. Een lus: 24 uur is al snel duizenden metingen. */
export function extent(values: Iterable<number | null>): { min: number; max: number } | null {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (!isNum(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return min <= max ? { min, max } : null;
}

/** Begin en eind van alle meetmomenten (ook momenten zonder waarde), of null zonder punten. */
export function historyRange(h: ZoneHistory): { t0: number; t1: number } | null {
  const e = extent([...h.temperature, ...h.target].map(([t]) => ms(t)));
  return e ? { t0: e.min, t1: Math.max(e.max, e.min + 1) } : null;
}

/** Index van het uur waar een tijdstip in valt (uren tellen vanaf het hele uur van het eerste punt). */
export function slotIndex(t: number, slot0: number): number {
  return Math.floor((t - slot0) / HOUR_MS);
}

/**
 * De uren van de grafiek, van het uur van het eerste punt tot en met het uur van het laatste. Uren rekenen in UTC:
 * in elke tijdzone met hele uren verschil (zoals Europe/Amsterdam, ook rond de zomertijd) is dat het klokuur.
 */
export function historySlots(h: ZoneHistory): HistorySlot[] {
  const range = historyRange(h);
  if (!range) return [];
  const slot0 = Math.floor(range.t0 / HOUR_MS) * HOUR_MS;
  const count = slotIndex(range.t1, slot0) + 1;
  const slots: HistorySlot[] = [];
  for (let i = 0; i < count; i++) {
    const start = slot0 + i * HOUR_MS;
    const end = start + HOUR_MS;
    slots.push({ start, end, from: Math.max(start, range.t0), to: Math.min(end, range.t1), measured: null, target: null });
  }
  const sums = new Map<number, number>();
  for (const [ts, v] of h.temperature) {
    const t = ms(ts);
    if (!isNum(v) || !Number.isFinite(t)) continue;
    const i = slotIndex(t, slot0);
    const s = slots[i];
    if (!s) continue;
    const m = s.measured;
    if (!m) s.measured = { avg: v, min: v, max: v, n: 1 };
    else {
      m.n += 1;
      m.min = Math.min(m.min, v);
      m.max = Math.max(m.max, v);
    }
    sums.set(i, (sums.get(i) ?? 0) + v);
  }
  for (const [i, sum] of sums) slots[i].measured!.avg = sum / slots[i].measured!.n;
  for (const [ts, v] of h.target) {
    const t = ms(ts);
    if (!isNum(v) || !Number.isFinite(t)) continue;
    const s = slots[slotIndex(t, slot0)];
    if (!s) continue;
    if (!s.target) s.target = [v];
    else if (s.target[s.target.length - 1] !== v) s.target.push(v);
  }
  return slots;
}

/**
 * Splitst een meetreeks in stukken die de lijn ononderbroken mag tekenen: een nieuw stuk begint na een punt zonder
 * waarde en na elk uur zonder meting (nooit doortrekken over een gat, Peil §10).
 */
export function lineRuns(points: [string, number | null][], slot0: number): { t: number; v: number }[][] {
  const runs: { t: number; v: number }[][] = [];
  let run: { t: number; v: number }[] = [];
  let prevSlot: number | null = null;
  for (const [ts, v] of points) {
    const t = ms(ts);
    if (!Number.isFinite(t)) continue;
    if (!isNum(v)) {
      if (run.length) runs.push(run);
      run = [];
      prevSlot = null;
      continue;
    }
    const i = slotIndex(t, slot0);
    if (run.length && prevSlot !== null && i - prevSlot > 1) {
      runs.push(run);
      run = [];
    }
    run.push({ t, v });
    prevSlot = i;
  }
  if (run.length) runs.push(run);
  return runs;
}

/** Aaneengesloten uren zonder enige meting (geen gemeten temperatuur en geen doel): de gearceerde gaten. */
export function gapRuns(slots: HistorySlot[]): { from: number; to: number }[] {
  const out: { from: number; to: number }[] = [];
  for (let i = 0; i < slots.length; i++) {
    if (slots[i].measured || slots[i].target) continue;
    let j = i;
    while (j + 1 < slots.length && !slots[j + 1].measured && !slots[j + 1].target) j++;
    out.push({ from: slots[i].from, to: slots[j].to });
    i = j;
  }
  return out;
}

const tijd = (t: number, tz?: string) => new Date(t).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', timeZone: tz });
const dag = (t: number, tz?: string) => new Date(t).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', timeZone: tz });

/**
 * "9 nov 13:00–14:00": met de dag, want 24 uur loopt over middernacht. Begint en eindigt het stuk in dezelfde minuut
 * (één meting, of de laatste meting precies op het hele uur), dan staat de tijd er één keer: "9 nov 13:00".
 */
export function slotLabel(s: HistorySlot, tz?: string): string {
  const van = tijd(s.from, tz);
  const tot = tijd(s.to, tz);
  return `${dag(s.from, tz)} ${van === tot ? van : `${van}–${tot}`}`;
}

export function measuredText(s: HistorySlot): string {
  return s.measured ? temp(s.measured.avg) : GEEN_METING;
}

export function rangeText(s: HistorySlot): string {
  if (!s.measured) return GEEN_METING;
  return s.measured.min === s.measured.max ? temp(s.measured.min) : `${temp(s.measured.min)} – ${temp(s.measured.max)}`;
}

export function targetText(s: HistorySlot): string {
  return s.target ? s.target.map((v) => temp(v)).join(' → ') : GEEN_METING;
}

/** Eén zin per uur voor de voorleesregel (aria-live) en de zichtbare uitlezing bij toetsenbord en aanwijzer. */
export function slotReadout(s: HistorySlot, tz?: string): string {
  const when = slotLabel(s, tz);
  if (!s.measured && !s.target) return `${when}: ${GEEN_METING}`;
  const m = s.measured;
  const gemeten = !m
    ? `gemeten: ${GEEN_METING}`
    : m.min === m.max
      ? `gemeten ${temp(m.avg)}`
      : `gemeten gemiddeld ${temp(m.avg)} (laagst ${temp(m.min)}, hoogst ${temp(m.max)})`;
  const doel = s.target ? `doel ${s.target.map((v) => temp(v)).join(' en daarna ')}` : `doel: ${GEEN_METING}`;
  return `${when}: ${gemeten}; ${doel}`;
}
