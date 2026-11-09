import type { EnergyMeter, EnergySummary, GasSummary } from '../api/types';
import { ProgressBar, QualityBadge } from '../components/bits';
import { kwh, money, pct } from '../format';

const METER_ORDER = ['grid', 'grid_export', 'airco', 'boiler', 'rest'];

export function orderedMeters(s: EnergySummary): EnergyMeter[] {
  const rank = (r: string) => (METER_ORDER.indexOf(r) + 1 || 99);
  return [...s.meters].sort((a, b) => rank(a.role) - rank(b.role));
}

/** All notes of a meter (summary note + per-bucket notes such as "verdachte meterstand overgeslagen"). */
export function meterNotes(m: { note: string | null; notes?: string[] }): string[] {
  const out: string[] = [];
  for (const n of [m.note, ...(m.notes ?? [])]) if (n && !out.includes(n)) out.push(n);
  return out;
}

/** "€ 2,18" plus how much of the kWh was priced when that is not everything. */
function CostLine({ cost, coverage, currency }: { cost: number | null; coverage: number; currency: string }) {
  if (cost === null) return null;
  return (
    <span className="small">
      {money(cost, currency)}
      {coverage < 0.999 && <span className="muted"> · {pct(coverage)} met tarief</span>}
    </span>
  );
}

function MeterCard({ m, currency }: { m: EnergyMeter; currency: string }) {
  const notes = meterNotes(m);
  return (
    <div className={`card tight live en-meter en-meter-${m.role}`} data-testid={`meter-${m.role}`}>
      <span className="card-sub">{m.label}</span>
      <span className="big-num en-big">{kwh(m.kwh)}</span>
      <span className="row small" style={{ gap: 8 }}>
        <QualityBadge quality={m.quality} />
        <CostLine cost={m.cost} coverage={m.cost_coverage} currency={currency} />
      </span>
      {notes.length > 0 && (
        <ul className="plain-list small en-notes">
          {notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}

function GasCard({ gas, currency }: { gas: GasSummary; currency: string }) {
  return (
    <div className="card tight live en-meter en-meter-gas" data-testid="meter-gas">
      <span className="card-sub">Gas</span>
      <span className="big-num en-big">{gas.m3.toLocaleString('nl-NL', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} m³</span>
      <span className="row small" style={{ gap: 8 }}>
        <QualityBadge quality={gas.quality} />
        <CostLine cost={gas.cost} coverage={gas.cost_coverage} currency={currency} />
      </span>
      {gas.note && <span className="small muted">{gas.note}</span>}
    </div>
  );
}

/** Totals per meter with quality, cost and notes (verbeterlijst 19/20); gas when the home has it. */
export function MeterCards({ s }: { s: EnergySummary }) {
  return (
    <section aria-label="Totalen per meter" className="en-meters">
      {orderedMeters(s).map((m) => <MeterCard key={m.role} m={m} currency={s.currency} />)}
      {s.gas && <GasCard gas={s.gas} currency={s.currency} />}
    </section>
  );
}

/** Cost breakdown like on the energy bill: variable import − export + fixed per day + gas (verbeterlijst 35). */
export function CostCard({ s, isAdmin }: { s: EnergySummary; isAdmin: boolean }) {
  const grid = s.meters.find((m) => m.role === 'grid');
  const exp = s.meters.find((m) => m.role === 'grid_export');
  const rows: [string, number][] = [];
  if (grid?.cost !== null && grid?.cost !== undefined) rows.push(['Stroom (verbruik)', grid.cost]);
  if (exp?.cost) rows.push(['Teruglevering', -exp.cost]);
  if (s.fixed_cost !== null && s.fixed_cost !== undefined && s.fixed_days > 0) rows.push([`Vaste kosten (${s.fixed_days} ${s.fixed_days === 1 ? 'dag' : 'dagen'})`, s.fixed_cost]);
  if (s.gas?.cost !== null && s.gas?.cost !== undefined) rows.push(['Gas', s.gas.cost]);
  return (
    <section aria-labelledby="cost-h" className="card en-cost">
      <div className="section-head">
        <h2 id="cost-h" className="en-h2">Kosten</h2>
        {s.cost !== null && <span className="big-num en-big live">{money(s.cost, s.currency)}</span>}
      </div>
      {s.cost === null ? (
        <p className="small muted" style={{ margin: 0 }}>
          {s.cost_note ?? 'Geen tarief, dus geen kosten.'}{' '}
          {isAdmin ? 'Voeg hieronder een tarief toe met de ingangsdatum van je contract.' : 'Vraag een beheerder om een tarief in te stellen.'}
        </p>
      ) : (
        <>
          <dl className="facts compact en-cost-rows">
            {rows.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v < 0 ? `− ${money(-v, s.currency)}` : money(v, s.currency)}</dd></div>
            ))}
          </dl>
          <div className="stack tight">
            <span className="small">Verbruik met een tarief: {pct(s.cost_coverage)}</span>
            <ProgressBar value={s.cost_coverage} label="Deel van het verbruik met een tarief" />
          </div>
          {s.cost_note && <p className="note info" style={{ margin: 0 }}>{s.cost_note}</p>}
        </>
      )}
    </section>
  );
}
