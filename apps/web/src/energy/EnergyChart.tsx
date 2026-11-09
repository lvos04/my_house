import type { EnergyBucket, EnergySummary } from '../api/types';
import { kwh, money } from '../format';
import { bucketAxisLabel, bucketLongLabel } from './period';

/** Roles stacked in the chart, bottom to top. Grid totals are not parts of the stack. */
export const STACK_ORDER = ['airco', 'boiler', 'rest'];
const GRID = new Set(['grid', 'grid_import', 'grid_export', 'gas']);

/** Segment class per role; "rest" (Overig) is hatched as well as coloured, so it never relies on a pale fill. */
export function segClass(role: string): string {
  return `en-seg en-seg-${STACK_ORDER.includes(role) ? role : 'other'}`;
}

export const FALLBACK_LABEL: Record<string, string> = { airco: 'Airco', boiler: 'Boiler', rest: 'Overig huis', grid: 'Huis totaal', grid_export: 'Teruglevering' };

export function stackRoles(s: EnergySummary): string[] {
  const present = new Set(s.buckets.flatMap((b) => Object.keys(b.values)));
  const known = STACK_ORDER.filter((r) => present.has(r));
  if (known.length) return known;
  // No sub-meters: show the grid total itself.
  return present.has('grid') ? ['grid'] : [...present].filter((r) => !GRID.has(r));
}

export type BucketState = 'complete' | 'incomplete' | 'running' | 'future';

/** Quality of one bucket. Future and still-running buckets are not "incomplete": they just have not happened yet. */
export function bucketState(b: EnergyBucket, now: number = Date.now()): BucketState {
  const start = new Date(b.start).getTime();
  const end = new Date(b.end ?? b.start).getTime();
  if (start >= now) return 'future';
  if (end > now) return 'running';
  return b.complete ? 'complete' : 'incomplete';
}

export const BUCKET_STATE_LABEL: Record<BucketState, string> = {
  complete: 'volledig',
  incomplete: 'onvolledig',
  running: 'loopt nog',
  future: 'nog niet begonnen',
};

function labelOf(s: EnergySummary, role: string): string {
  return s.meters.find((m) => m.role === role)?.label ?? FALLBACK_LABEL[role] ?? role;
}

/** One sentence per bucket for screen readers and the tooltip. */
export function bucketDescription(s: EnergySummary, b: EnergyBucket, roles: string[], now: number = Date.now()): string {
  const total = roles.reduce((a, r) => a + (b.values[r] ?? 0), 0);
  const st = bucketState(b, now);
  const parts = [`${bucketLongLabel(b.start, b.end, s.period, s.timezone)}: ${kwh(total)}`];
  if (st !== 'future' && roles.length > 1) parts.push(roles.map((r) => `${labelOf(s, r)} ${kwh(b.values[r] ?? 0)}`).join(', '));
  if (b.cost !== null && b.cost !== undefined) parts.push(money(b.cost, s.currency));
  if (st !== 'complete') parts.push(BUCKET_STATE_LABEL[st]);
  if (b.notes?.length) parts.push(`let op: ${b.notes.join('; ')}`);
  return parts.join(' · ');
}

export function ChartLegend({ summary }: { summary: EnergySummary }) {
  const roles = stackRoles(summary);
  return (
    <ul className="en-legend" aria-label="Legenda">
      {roles.map((r) => {
        const m = summary.meters.find((x) => x.role === r);
        return (
          <li key={r}>
            <span className={`en-swatch ${segClass(r)}`} aria-hidden="true" />
            {labelOf(summary, r)}
            {m && m.quality !== 'measured' ? <span className="muted"> ({m.quality === 'derived' ? 'afgeleid' : m.quality === 'estimated' ? 'geschat' : 'ontbreekt'})</span> : null}
          </li>
        );
      })}
      <li><span className="en-swatch en-swatch-incomplete" aria-hidden="true" />Onvolledig</li>
      <li><span className="en-flag-demo" aria-hidden="true">!</span>Opmerking (bijv. verdachte meterstand)</li>
    </ul>
  );
}

/** Stacked bars per hour/day/month with quality per bucket: dashed = incomplete, "!" = a note
 * (suspect reading skipped, counter reset, missing readings). Hatched = "Overig" (derived). */
export function EnergyChart({ summary, now = Date.now() }: { summary: EnergySummary; now?: number }) {
  const roles = stackRoles(summary);
  if (!summary.buckets.length) return <p className="muted small">Nog geen metingen in deze periode.</p>;
  const totals = summary.buckets.map((b) => roles.reduce((a, r) => a + (b.values[r] ?? 0), 0));
  const max = Math.max(0.001, ...totals);
  const n = summary.buckets.length;
  const cols = `repeat(${n}, minmax(0, 1fr))`;
  const every = n > 24 ? 3 : n > 16 ? 2 : 1;
  return (
    <div className="en-chart">
      <div className={`en-bars${n > 16 ? ' dense' : ''}`} style={{ gridTemplateColumns: cols }}>
        {summary.buckets.map((b, i) => {
          const st = bucketState(b, now);
          const flagged = (b.notes?.length ?? 0) > 0;
          const desc = bucketDescription(summary, b, roles, now);
          return (
            <div key={b.start} className={`en-col is-${st}`} data-state={st}>
              {flagged && <span className="en-flag" aria-hidden="true">!</span>}
              <div className="en-bar" role="img" aria-label={desc} title={desc} style={{ height: `${st === 'future' ? 0 : Math.max(1.5, (totals[i] / max) * 100)}%` }}>
                {roles.map((r) => (b.values[r] ? <div key={r} className={segClass(r)} style={{ flex: b.values[r] }} /> : null))}
              </div>
            </div>
          );
        })}
      </div>
      <div className="en-axis" style={{ gridTemplateColumns: cols }} aria-hidden="true">
        {summary.buckets.map((b, i) => (
          <span key={b.start}>{i % every === 0 ? bucketAxisLabel(b.start, summary.period, summary.timezone) : ''}</span>
        ))}
      </div>
    </div>
  );
}

/** The same data as a table (screen readers, exact numbers, quality and notes per bucket). */
export function EnergyTable({ summary, now = Date.now() }: { summary: EnergySummary; now?: number }) {
  const roles = stackRoles(summary);
  const gas = summary.gas !== null && summary.buckets.some((b) => b.gas_m3 !== undefined);
  const nl = (v: number, digits = 2) => v.toLocaleString('nl-NL', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (
    <div className="table-wrap">
      <table className="stack-mobile en-table">
        <caption className="sr-only">Verbruik per {summary.period === 'day' ? 'uur' : summary.period === 'year' ? 'maand' : 'dag'} in kWh</caption>
        <thead>
          <tr>
            <th scope="col">Periode</th>
            {roles.map((r) => <th key={r} scope="col">{labelOf(summary, r)}</th>)}
            {roles.length > 1 && <th scope="col">Samen</th>}
            {gas && <th scope="col">Gas (m³)</th>}
            <th scope="col">Kosten</th>
            <th scope="col">Kwaliteit</th>
          </tr>
        </thead>
        <tbody>
          {summary.buckets.map((b) => {
            const st = bucketState(b, now);
            const total = roles.reduce((a, r) => a + (b.values[r] ?? 0), 0);
            return (
              <tr key={b.start} className={st === 'incomplete' || b.notes?.length ? 'en-row-flag' : undefined}>
                <th scope="row" data-label="Periode">{bucketLongLabel(b.start, b.end, summary.period, summary.timezone)}</th>
                {roles.map((r) => <td key={r} data-label={labelOf(summary, r)}>{st === 'future' ? '–' : nl(b.values[r] ?? 0)}</td>)}
                {roles.length > 1 && <td data-label="Samen">{st === 'future' ? '–' : nl(total)}</td>}
                {gas && <td data-label="Gas (m³)">{b.gas_m3 === undefined || st === 'future' ? '–' : nl(b.gas_m3, 3)}</td>}
                <td data-label="Kosten">{b.cost === null || b.cost === undefined ? '–' : money(b.cost, summary.currency)}</td>
                <td data-label="Kwaliteit">
                  {BUCKET_STATE_LABEL[st]}
                  {b.notes?.length ? <span className="en-note-inline">: {b.notes.join('; ')}</span> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
