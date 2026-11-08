import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { EnergySummary, Period } from '../api/types';
import { useHome } from '../state';
import { ActionButton, Empty, ErrorNote, Loading, QualityBadge } from '../components/bits';
import { EnergyBars, partMeters, ROLE_COLOR } from '../components/energy';
import { kwh, localDate, money } from '../format';

const PERIODS: { id: Period; label: string }[] = [
  { id: 'day', label: 'Dag' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Maand' },
  { id: 'year', label: 'Jaar' },
];

export function Totals({ s }: { s: EnergySummary }) {
  return (
    <section aria-label="Totalen" className="grid-status" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
      {s.meters.map((m) => (
        <div key={m.role} className="card tight live" style={{ padding: 18 }}>
          <span className="card-sub">{m.label}</span>
          <span className="big-num" style={{ fontSize: 30 }}>{kwh(m.kwh)}</span>
          <span className="row small" style={{ gap: 8 }}>
            <QualityBadge quality={m.quality} />
            {m.cost !== null && <span className="muted">{money(m.cost, s.currency)}</span>}
          </span>
          {m.note && <span className="small muted">{m.note}</span>}
        </div>
      ))}
    </section>
  );
}

function TariffSection({ homeId }: { homeId: string }) {
  const qc = useQueryClient();
  const tariffs = useQuery({ queryKey: ['tariffs', homeId], queryFn: () => api.tariffs(homeId) });
  const [form, setForm] = useState({ valid_from: localDate(), import_per_kwh: '', export_per_kwh: '', fixed_per_day: '' });
  const add = useMutation({
    mutationFn: () =>
      api.addTariff(homeId, {
        valid_from: form.valid_from,
        import_per_kwh: Number(form.import_per_kwh.replace(',', '.')),
        export_per_kwh: Number(form.export_per_kwh.replace(',', '.')),
        fixed_per_day: form.fixed_per_day ? Number(form.fixed_per_day.replace(',', '.')) : null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tariffs', homeId] }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    add.mutate();
  };
  const fmt = (v: number) => `€ ${v.toLocaleString('nl-NL', { minimumFractionDigits: 3, maximumFractionDigits: 4 })}`;
  return (
    <section aria-labelledby="tarief-h" className="card">
      <h2 id="tarief-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Tarieven</h2>
      {tariffs.data?.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Vanaf</th><th>Levering /kWh</th><th>Teruglevering /kWh</th><th>Vast /dag</th><th>Bron</th></tr></thead>
            <tbody>
              {tariffs.data.map((t) => (
                <tr key={t.id}><td>{new Date(t.valid_from).toLocaleDateString('nl-NL')}</td><td>{fmt(t.import_per_kwh)}</td><td>{fmt(t.export_per_kwh)}</td><td>{t.fixed_per_day !== null ? fmt(t.fixed_per_day) : '–'}</td><td>{t.source}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>Nog geen tarief. Zonder tarief worden geen kosten berekend.</Empty>
      )}
      <form onSubmit={submit} className="form-grid" aria-label="Tarief toevoegen">
        <label className="field">Geldig vanaf<input type="date" required value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} /></label>
        <label className="field">Levering € /kWh<input inputMode="decimal" required pattern="[0-9]+([.,][0-9]+)?" value={form.import_per_kwh} onChange={(e) => setForm({ ...form, import_per_kwh: e.target.value })} /></label>
        <label className="field">Teruglevering € /kWh<input inputMode="decimal" required pattern="[0-9]+([.,][0-9]+)?" value={form.export_per_kwh} onChange={(e) => setForm({ ...form, export_per_kwh: e.target.value })} /></label>
        <label className="field">Vaste kosten € /dag (optioneel)<input inputMode="decimal" pattern="[0-9]+([.,][0-9]+)?" value={form.fixed_per_day} onChange={(e) => setForm({ ...form, fixed_per_day: e.target.value })} /></label>
        <ActionButton type="submit" className="btn primary" disabled={add.isPending}>Toevoegen</ActionButton>
      </form>
      {add.error && <ErrorNote error={add.error} />}
    </section>
  );
}

export function EnergyPage() {
  const { homeId } = useHome();
  const [period, setPeriod] = useState<Period>('month');
  const [date] = useState(localDate());
  const homes = useQuery({ queryKey: ['homes'], queryFn: api.homes });
  const tz = homes.data?.find((h) => h.id === homeId)?.timezone;
  const s = useQuery({ queryKey: ['energy', homeId, period, date], queryFn: () => api.energySummary(homeId!, period, date), enabled: !!homeId, refetchInterval: 300_000 });
  if (!homeId) return <Loading />;
  const title = s.data
    ? new Date(s.data.start).toLocaleDateString('nl-NL', period === 'year' ? { year: 'numeric', timeZone: tz } : period === 'month' ? { month: 'long', year: 'numeric', timeZone: tz } : { day: 'numeric', month: 'long', timeZone: tz })
    : 'Energie';
  const legendRoles = s.data ? partMeters(s.data) : [];

  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Energie</span><h1 style={{ textTransform: 'capitalize' }}>{title}</h1></div>
        <div className="row" style={{ gap: 8 }}>
          <div className="segmented" role="tablist" aria-label="Periode">
            {PERIODS.map((p) => (
              <button key={p.id} type="button" role="tab" aria-selected={period === p.id} onClick={() => setPeriod(p.id)}>{p.label}</button>
            ))}
          </div>
          <a className="btn" href={api.exportUrl(homeId, 'csv', s.data?.start, s.data?.end)} download>CSV</a>
          <a className="btn" href={api.exportUrl(homeId, 'json', s.data?.start, s.data?.end)} download>JSON</a>
        </div>
      </header>
      {s.isLoading && <Loading />}
      {s.error && !s.data && <ErrorNote error={s.error} />}
      {s.data && (
        <>
          <Totals s={s.data} />
          <section aria-labelledby="trend-h" className="card live" style={{ padding: 24 }}>
            <div className="section-head">
              <h2 id="trend-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Verbruik per {period === 'day' ? 'uur' : period === 'year' ? 'maand' : 'dag'}</h2>
              <div className="legend">
                {legendRoles.map((m) => (
                  <span key={m.role}><span className="swatch" style={{ background: ROLE_COLOR[m.role] ?? 'var(--rest)' }} />{m.label}{m.quality === 'derived' ? ' (afgeleid)' : ''}</span>
                ))}
                <span><span className="swatch" style={{ border: '2px dashed var(--warn-dot)' }} />Onvolledig</span>
              </div>
            </div>
            <EnergyBars summary={s.data} tz={tz} />
            <span className="small muted">Totaal {kwh(s.data.total_kwh)}{s.data.cost !== null ? ` · ${money(s.data.cost, s.data.currency)}` : ' · geen tarief, geen kosten'}</span>
          </section>
        </>
      )}
      <div className="grid-auto">
        <section aria-labelledby="meet-h" className="card">
          <h2 id="meet-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Wat wordt gemeten</h2>
          <ul className="meter-list" style={{ lineHeight: 1.45 }}>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><QualityBadge quality="measured" /><span>Huis totaal (P1), airco totaal (kWh-meter), boiler (Energy Socket)</span></li>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><QualityBadge quality="derived" /><span>Overig = P1 − airco − boiler. Dit zijn onbekende apparaten.</span></li>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><span className="q missing">niet gemeten</span><span>Verbruik per binnenunit: beide units delen één compressor.</span></li>
          </ul>
          <p className="small muted" style={{ margin: 0 }}>De boiler-stekker wordt alleen uitgelezen. Schakelen is in deze app geblokkeerd; de HomeWizard-app kan de stekker nog wel schakelen.</p>
        </section>
        <TariffSection homeId={homeId} />
      </div>
    </>
  );
}
