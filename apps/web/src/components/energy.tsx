import type { EnergyBucket, EnergyMeter, EnergySummary, Period } from '../api/types';
import { kwh, money } from '../format';
import { QualityBadge } from './bits';

/** Kleur volgt de entiteit (Peil §10, gevalideerd palet): airco chart-1, boiler chart-3, overig chart-other
 * (+ arcering), net neutraal. Nooit een statuskleur als reeks (teruglevering was groen). */
export const ROLE_COLOR: Record<string, string> = {
  airco: 'var(--chart-1)',
  boiler: 'var(--chart-3)',
  rest: 'var(--chart-other)',
  grid: 'var(--chart-label)',
  grid_import: 'var(--chart-label)',
  grid_export: 'var(--chart-5)',
};
/** "Overig" krijgt naast de kleur ook arcering, zodat het niet alleen op kleur leunt. */
function roleStyle(role: string, extra: Record<string, unknown> = {}) {
  const background = ROLE_COLOR[role] ?? 'var(--chart-other)';
  return role === 'rest' || !(role in ROLE_COLOR)
    ? { ...extra, backgroundColor: background, backgroundImage: 'repeating-linear-gradient(135deg, transparent 0 4px, var(--card) 4px 5.5px)' }
    : { ...extra, background };
}
export const STACK_ROLES = ['airco', 'boiler', 'rest'];
const GRID_ROLES = new Set(['grid', 'grid_import', 'grid_export']);

/** Meters that make up consumption (excludes grid totals) in display order. */
export function partMeters(s: EnergySummary): EnergyMeter[] {
  const order = (r: string) => (STACK_ROLES.indexOf(r) + 1 || 99);
  return s.meters.filter((m) => !GRID_ROLES.has(m.role)).sort((a, b) => order(a.role) - order(b.role));
}

export function MeterList({ meters, currency }: { meters: EnergyMeter[]; currency: string }) {
  return (
    <ul className="meter-list">
      {meters.map((m) => (
        <li key={m.role} style={m.quality !== 'measured' ? { color: 'var(--muted-foreground)' } : undefined}>
          <span className="row" style={{ gap: 8 }}>
            <span className="swatch" style={roleStyle(m.role)} aria-hidden="true" />
            {m.label}
          </span>
          <span className="row" style={{ gap: 8 }}>
            {kwh(m.kwh)}
            {m.cost !== null && <span className="muted small">{money(m.cost, currency)}</span>}
            <QualityBadge quality={m.quality} />
          </span>
          {m.note && <span className="small muted" style={{ width: '100%' }}>{m.note}</span>}
        </li>
      ))}
    </ul>
  );
}

export function StackBar({ meters }: { meters: EnergyMeter[] }) {
  const total = meters.reduce((a, m) => a + (m.kwh ?? 0), 0);
  if (total <= 0) return <div className="energy-bar" aria-hidden="true" />;
  return (
    <div className="energy-bar" role="img" aria-label={meters.map((m) => `${m.label} ${kwh(m.kwh)}`).join(', ')}>
      {meters.map((m) => (m.kwh ? <div key={m.role} style={roleStyle(m.role, { flex: m.kwh })} /> : null))}
    </div>
  );
}

function bucketLabel(b: EnergyBucket, period: Period, tz?: string): string {
  const d = new Date(b.start);
  switch (period) {
    case 'day':
      return d.toLocaleTimeString('nl-NL', { hour: '2-digit', timeZone: tz });
    case 'week':
      return d.toLocaleDateString('nl-NL', { weekday: 'short', timeZone: tz });
    case 'month':
      return d.toLocaleDateString('nl-NL', { day: 'numeric', timeZone: tz });
    case 'year':
      return d.toLocaleDateString('nl-NL', { month: 'short', timeZone: tz });
  }
}

export function EnergyBars({ summary, tz }: { summary: EnergySummary; tz?: string }) {
  const roles = (() => {
    const present = new Set(summary.buckets.flatMap((b) => Object.keys(b.values)));
    const stack = STACK_ROLES.filter((r) => present.has(r));
    return stack.length ? stack : [...present].filter((r) => r !== 'grid_export');
  })();
  const totals = summary.buckets.map((b) => roles.reduce((a, r) => a + (b.values[r] ?? 0), 0));
  const max = Math.max(0.001, ...totals);
  const cols = `repeat(${Math.max(1, summary.buckets.length)}, minmax(0, 1fr))`;
  if (!summary.buckets.length) return <p className="muted small">Nog geen metingen in deze periode.</p>;
  return (
    <div>
      <div className="bars" style={{ gridTemplateColumns: cols }}>
        {summary.buckets.map((b, i) => {
          const label = `${bucketLabel(b, summary.period, tz)}: ${kwh(totals[i])}${b.complete ? '' : ', onvolledig'}`;
          return (
            <div
              key={b.start}
              className={`bar${b.complete ? '' : ' incomplete'}`}
              style={{ height: `${Math.max(2, (totals[i] / max) * 100)}%` }}
              title={label}
              role="img"
              aria-label={label}
            >
              {roles.map((r) => (b.values[r] ? <div key={r} style={roleStyle(r, { flex: b.values[r] })} /> : null))}
            </div>
          );
        })}
      </div>
      <div className="bar-labels" style={{ gridTemplateColumns: cols, marginTop: 6 }}>
        {summary.buckets.map((b, i) => (
          <span key={b.start}>{summary.buckets.length > 16 && i % 2 ? '' : bucketLabel(b, summary.period, tz)}</span>
        ))}
      </div>
    </div>
  );
}
