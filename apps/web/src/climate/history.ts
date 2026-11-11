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

/** Staat er in deze geschiedenis minstens één waarde (gemeten of doel)? Zonder waarden is er geen grafiek en geen legenda. */
export function hasValues(h: ZoneHistory): boolean {
  return extent([...h.temperature, ...h.target].map(([, v]) => v)) !== null;
}

/**
 * Begin en eind van alle meetmomenten (ook momenten zonder waarde), of null zonder punten. Met `tot` loopt het tijdvak
 * door tot dat moment (nooit korter): voor het stuk na de laatste meting als de sensor verouderd is (Peil §10).
 */
export function historyRange(h: ZoneHistory, tot?: number): { t0: number; t1: number } | null {
  const e = extent([...h.temperature, ...h.target].map(([t]) => ms(t)));
  if (!e) return null;
  const eind = isNum(tot) ? Math.max(e.max, tot) : e.max;
  return { t0: e.min, t1: Math.max(eind, e.min + 1) };
}

/** De laatste meting mét waarde (tijd en waarde), of null als er in dit tijdvak geen gemeten temperatuur is. */
export function lastMeasured(h: ZoneHistory): { t: number; v: number } | null {
  for (let i = h.temperature.length - 1; i >= 0; i--) {
    const [ts, v] = h.temperature[i];
    const t = ms(ts);
    if (isNum(v) && Number.isFinite(t)) return { t, v };
  }
  return null;
}

/** Index van het uur waar een tijdstip in valt (uren tellen vanaf het hele uur van het eerste punt). */
export function slotIndex(t: number, slot0: number): number {
  return Math.floor((t - slot0) / HOUR_MS);
}

/**
 * De uren van de grafiek, van het uur van het eerste punt tot en met het uur van het laatste (of tot `tot`, als de
 * grafiek doorloopt na de laatste meting: die uren hebben dan geen meting). Uren rekenen in UTC: in elke tijdzone met
 * hele uren verschil (zoals Europe/Amsterdam, ook rond de zomertijd) is dat het klokuur.
 */
export function historySlots(h: ZoneHistory, tot?: number): HistorySlot[] {
  const range = historyRange(h, tot);
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

/**
 * Het stuk na de laatste meting bij een verouderde sensor: gaten die daarin vallen, vervallen (dat stuk heeft zijn
 * eigen arcering met "Verouderd sinds …"); een gat dat eroverheen loopt, stopt waar het verouderde stuk begint.
 */
export function gapsBefore(gaps: { from: number; to: number }[], grens: number | null): { from: number; to: number }[] {
  if (grens === null) return gaps;
  return gaps.filter((g) => g.from < grens).map((g) => (g.to > grens ? { from: g.from, to: grens } : g));
}

/**
 * Tijden voor de tussenlabels onder de grafiek: hele klokuren op een vaste stap (1, 2, 3, 6 of 12 uur; hoogstens
 * acht stappen over de hele breedte), in de tijdzone van het huis. Uren dicht bij het begin of het eind vervallen
 * (15 % van de breedte): daar staan de begin- en eindtijd al, en labels mogen elkaar niet raken. Korter dan twee uur:
 * geen tussenlabels.
 */
export function hourTicks(t0: number, t1: number, tz?: string): number[] {
  const span = t1 - t0;
  if (!(span >= 2 * HOUR_MS)) return [];
  const stap = [1, 2, 3, 6, 12].find((s) => span / (s * HOUR_MS) <= 8) ?? 12;
  const fmt = new Intl.DateTimeFormat('nl-NL', { hour: 'numeric', hourCycle: 'h23', timeZone: tz });
  const uur = (t: number) => Number(fmt.formatToParts(t).find((p) => p.type === 'hour')?.value);
  const marge = span * 0.15;
  const out: number[] = [];
  for (let t = Math.ceil(t0 / HOUR_MS) * HOUR_MS; t < t1; t += HOUR_MS) {
    if (t - t0 < marge || t1 - t < marge) continue;
    if (uur(t) % stap === 0) out.push(t);
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

/** Het oordeel van de server over de sensor van de zone (`zone.temperature`): verouderd of niet, en de laatste meting. */
export interface SensorStand {
  stale: boolean;
  at: string | null;
  value: number | null;
}

export interface Verouderd {
  /** Het moment van de laatste meting mét waarde: daar stopt de lijn. */
  sinds: number;
  /** De waarde van die laatste meting. */
  waarde: number | null;
  /** Tot waar de grafiek doorloopt (alleen als de laatste meting in de grafiek staat). */
  tot?: number;
}

/**
 * Verouderd (Peil §10): de lijn stopt bij de laatste meting, daarna volgt een gearceerd stuk "Verouderd sinds …".
 * Of de sensor verouderd is, beslist de server (`stale`, met de termijn uit het beleid van de zone); de app rekent dat
 * nooit zelf uit. Het moment is de laatste meting mét waarde (uit de grafiek of, als die later is, van de server).
 * Alleen de breedte van het stuk gebruikt de klok van dit apparaat ("nu"), met twee grenzen die van de server komen:
 * minstens de termijn waarna de server "verouderd" zegt, en hoogstens 24 uur na de laatste meting in de grafiek (ouder
 * levert de server niet). Geeft null als de sensor niet verouderd is of als er geen laatste meting bekend is.
 */
export function staleSince(h: ZoneHistory, sensor: SensorStand | null | undefined, nu: number, staleAfterS = 0): Verouderd | null {
  if (!sensor || sensor.stale !== true) return null;
  const laatste = lastMeasured(h);
  const at = sensor.at && isNum(sensor.value) ? ms(sensor.at) : NaN;
  const vanServer = Number.isFinite(at) && (!laatste || at > laatste.t);
  if (!laatste && !vanServer) return null;
  const sinds = vanServer ? at : laatste!.t;
  const waarde = vanServer ? sensor.value : laatste!.v;
  if (!laatste) return { sinds, waarde };
  const termijn = isNum(staleAfterS) && staleAfterS > 0 ? staleAfterS * 1000 : 0;
  const tot = Math.min(Math.max(isNum(nu) ? nu : sinds, sinds + termijn), laatste.t + 24 * HOUR_MS);
  return { sinds, waarde, tot };
}

/** "11 nov 04:27": dag en tijd van één moment (bron en "verouderd sinds" onder de grafiek). */
export function momentLabel(t: number, tz?: string): string {
  return `${dag(t, tz)} ${tijd(t, tz)}`;
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
