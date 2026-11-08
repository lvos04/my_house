import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AdaptiveModel, AdaptiveStatus, ExperimentResult, Zone } from '../api/types';
import { useHome } from '../state';
import { useIsAdmin } from '../user';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';
import { dateTime } from '../format';

const nl = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? '–' : v.toLocaleString('nl-NL', { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** "Terugzetten" when the version is older than the currently active one. */
export function activateLabel(m: AdaptiveModel, models: AdaptiveModel[]): string {
  const active = models.find((x) => x.active);
  return active && m.version < active.version ? 'Terugzetten' : 'Activeren';
}

/** <input type="datetime-local"> value (local time, no zone) → ISO UTC. */
export function localInputToIso(v: string): string {
  return new Date(v).toISOString();
}

function AdaptiveCard({ zone, status }: { zone: Zone; status: AdaptiveStatus }) {
  const qc = useQueryClient();
  const isAdmin = useIsAdmin();
  const key = ['adaptive', zone.id];
  const set = (s: AdaptiveStatus) => qc.setQueryData(key, s);
  const fit = useMutation({ mutationFn: () => api.adaptiveFit(zone.id), onSuccess: () => qc.invalidateQueries({ queryKey: key }) });
  const activate = useMutation({ mutationFn: (id: string) => api.adaptiveActivate(zone.id, id), onSuccess: set });
  const disable = useMutation({ mutationFn: () => api.adaptiveDisable(zone.id), onSuccess: set });
  const b = status.baseline;
  const pct = Math.min(100, Math.round((b.running_samples / Math.max(1, b.required)) * 100));
  const err = fit.error ?? activate.error ?? disable.error;

  return (
    <article className="card" aria-labelledby={`ad-${zone.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <h3 id={`ad-${zone.id}`}>{zone.name}</h3>
        <span className={`pill ${status.enabled ? 'heat' : 'neutral'}`}>{status.enabled ? 'Adaptief aan' : 'Adaptief uit'}</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Max. bijsturing ±{nl(status.max_offset_c)}°, harde grenzen blijven gelden, standaard uit.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div className="section-head small">
          <span>Baseline (passief meten)</span>
          <span className={b.ready ? '' : 'muted'} style={b.ready ? { color: 'var(--ok)', fontWeight: 600 } : undefined}>
            {b.ready ? 'Klaar' : `${b.running_samples} van ${b.required} kwartieren`}
          </span>
        </div>
        <div className="band" role="progressbar" aria-label={`Baseline ${zone.name}`} aria-valuemin={0} aria-valuemax={b.required} aria-valuenow={Math.min(b.running_samples, b.required)}>
          <div className="band-fill" style={{ left: 0, width: `${pct}%`, background: b.ready ? 'var(--ok)' : 'var(--cool)' }} />
        </div>
        <span className="small muted">{b.samples} kwartieren gemeten{b.since ? ` sinds ${dateTime(b.since)}` : ''}; alleen kwartieren waarin de airco draait tellen mee.</span>
      </div>

      {status.models.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Versie</th><th>R²</th><th>Reactie °C/u per °C</th><th>Bruikbaar</th><th>Status</th>{isAdmin && <th><span className="sr-only">Actie</span></th>}</tr></thead>
            <tbody>
              {status.models.map((m) => (
                <tr key={m.id}>
                  <td>v{m.version}{m.created_at ? <div className="small muted">{dateTime(m.created_at)}</div> : null}</td>
                  <td>{nl(m.params.r2, 2)}</td>
                  <td>{nl(m.params.k_per_h, 2)}</td>
                  <td>{m.params.usable ? 'ja' : <span className="muted">nee</span>}</td>
                  <td>{m.active ? <strong>actief</strong> : <span className="muted">–</span>}</td>
                  {isAdmin && (
                    <td>
                      {m.params.usable && !m.active && (
                        <ActionButton className="btn" disabled={activate.isPending} onClick={() => activate.mutate(m.id)}>
                          {activateLabel(m, status.models)}
                        </ActionButton>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>Nog geen model berekend.</Empty>
      )}
      {isAdmin && (
        <div className="row" style={{ gap: 8 }}>
          <ActionButton className="btn" disabled={fit.isPending} onClick={() => fit.mutate()}>{fit.isPending ? 'Bezig…' : 'Model berekenen'}</ActionButton>
          {status.enabled && <ActionButton className="btn danger" disabled={disable.isPending} onClick={() => disable.mutate()}>Adaptief uit</ActionButton>}
        </div>
      )}
      {fit.data && <span className="small muted" role="status">Model v{fit.data.version} berekend{fit.data.params.usable ? '.' : ', maar niet betrouwbaar genoeg om te activeren.'}</span>}
      {err && <ErrorNote error={err} />}
    </article>
  );
}

function ZoneAdaptive({ zone }: { zone: Zone }) {
  const q = useQuery({ queryKey: ['adaptive', zone.id], queryFn: () => api.adaptive(zone.id), refetchInterval: 300_000 });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  return <AdaptiveCard zone={zone} status={q.data} />;
}

const VERDICT_TITLE: Record<ExperimentResult['verdict'], string> = {
  vergelijkbaar: 'Vergelijkbare meting',
  indicatief: 'Indicatieve vergelijking',
  onvoldoende: 'Onvoldoende gegevens',
};

export function ExperimentResultView({ r }: { r: ExperimentResult }) {
  const comparable = r.verdict === 'vergelijkbaar';
  const pct = (v: number | null) => {
    if (v === null) return '–';
    if (comparable) return v < 0 ? `${nl(-v)}% besparing` : v > 0 ? `${nl(v)}% meer` : 'gelijk';
    return `${v > 0 ? '+' : v < 0 ? '−' : ''}${nl(Math.abs(v))}%`;
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className={`note ${comparable ? 'info' : 'warn'}`} role="status">
        <strong style={{ fontSize: 16 }}>{VERDICT_TITLE[r.verdict]}</strong>
        {comparable ? (
          <div>De perioden zijn goed genoeg vergelijkbaar om een besparing te noemen.</div>
        ) : (
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
        )}
      </div>
      {r.rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Variant</th><th>Dagen</th><th>kWh/dag</th>
                <th>{comparable ? 't.o.v. eerste' : 'Verschil t.o.v. eerste (indicatief)'}</th>
                <th>Gem. afwijking °C</th><th>Starts</th><th>Buiten °C</th>
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row) => (
                <tr key={row.variant}>
                  <td><strong>{row.variant}</strong></td>
                  <td>{nl(row.days)}</td>
                  <td>{nl(row.kwh_per_day, 2)}</td>
                  <td>{pct(row.vs_first_pct)}</td>
                  <td>{nl(row.mean_abs_dev_c, 2)}</td>
                  <td>{row.starts}</td>
                  <td>{nl(row.outdoor_mean_c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ExperimentResultLoader({ id }: { id: string }) {
  const q = useQuery({ queryKey: ['experiment', id], queryFn: () => api.experimentResult(id) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  return <ExperimentResultView r={q.data} />;
}

interface VariantDraft { label: string; start: string; end: string }
const PRESETS = ['Constant', 'Nachtverlaging', 'Voorverwarmen'];

function NewExperiment({ homeId, zones }: { homeId: string; zones: Zone[] }) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [zoneId, setZoneId] = useState(zones[0]?.id ?? '');
  const [variants, setVariants] = useState<VariantDraft[]>([
    { label: PRESETS[0], start: '', end: '' },
    { label: PRESETS[1], start: '', end: '' },
  ]);
  const [localError, setLocalError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api.createExperiment(homeId, {
        name: name.trim(),
        zone_id: zoneId,
        variants: variants.map((v) => ({ label: v.label.trim(), start: localInputToIso(v.start), end: localInputToIso(v.end) })),
      }),
    onSuccess: () => {
      setName('');
      qc.invalidateQueries({ queryKey: ['experiments', homeId] });
    },
  });
  const upd = (i: number, patch: Partial<VariantDraft>) => setVariants(variants.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (variants.some((v) => new Date(v.end) <= new Date(v.start))) return setLocalError('Het einde van elke periode moet na het begin liggen.');
    create.mutate();
  };
  return (
    <form className="card" onSubmit={submit} aria-labelledby="newexp-h">
      <h3 id="newexp-h">Nieuw experiment</h3>
      <p className="small muted" style={{ margin: 0 }}>Vergelijk profielen in aparte perioden met vergelijkbaar weer. Kies perioden van minstens 5 dagen.</p>
      <div className="form-grid">
        <label className="field">Naam<input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="Nachtverlaging woonkamer" /></label>
        <label className="field">Zone
          <select required value={zoneId} onChange={(e) => setZoneId(e.target.value)}>{zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}</select>
        </label>
      </div>
      {variants.map((v, i) => (
        <fieldset key={i} className="form-grid" style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 12, margin: 0 }}>
          <legend className="small muted">Variant {i + 1}</legend>
          <label className="field">Label<input required maxLength={50} value={v.label} onChange={(e) => upd(i, { label: e.target.value })} /></label>
          <label className="field">Begin<input type="datetime-local" required value={v.start} onChange={(e) => upd(i, { start: e.target.value })} /></label>
          <label className="field">Einde<input type="datetime-local" required value={v.end} onChange={(e) => upd(i, { end: e.target.value })} /></label>
          {variants.length > 2 && <button type="button" className="btn" aria-label={`Variant ${i + 1} verwijderen`} onClick={() => setVariants(variants.filter((_, j) => j !== i))}>Verwijderen</button>}
        </fieldset>
      ))}
      <div className="row" style={{ gap: 8 }}>
        {variants.length < 4 && (
          <button type="button" className="btn" onClick={() => setVariants([...variants, { label: PRESETS[variants.length] ?? `Variant ${variants.length + 1}`, start: '', end: '' }])}>Variant toevoegen</button>
        )}
        <ActionButton type="submit" className="btn primary" disabled={create.isPending || !zoneId}>Experiment aanmaken</ActionButton>
      </div>
      {localError && <p className="error-text" role="alert">{localError}</p>}
      {create.error && <ErrorNote error={create.error} />}
    </form>
  );
}

function Experiments({ homeId, zones }: { homeId: string; zones: Zone[] }) {
  const isAdmin = useIsAdmin();
  const list = useQuery({ queryKey: ['experiments', homeId], queryFn: () => api.experiments(homeId) });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section aria-labelledby="exp-h" className="section">
      <h2 id="exp-h">Experimenten</h2>
      <p className="small muted" style={{ margin: 0 }}>We noemen het alleen een besparing als de perioden lang genoeg zijn, de airco apart gemeten is en het buiten ongeveer even koud was.</p>
      {list.isLoading && <Loading />}
      {list.error && <ErrorNote error={list.error} />}
      {list.data?.length === 0 && <Empty>Nog geen experimenten.</Empty>}
      {list.data?.map((e) => (
        <article key={e.id} className="card">
          <div className="section-head" style={{ alignItems: 'center' }}>
            <div>
              <h3>{e.name}</h3>
              <span className="card-sub">{e.variants.map((v) => v.label).join(' vs ')} · {e.status}</span>
            </div>
            <button type="button" className="btn" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>
              {open === e.id ? 'Verbergen' : 'Resultaat'}
            </button>
          </div>
          {open === e.id && <ExperimentResultLoader id={e.id} />}
        </article>
      ))}
      {isAdmin && zones.length > 0 && <NewExperiment homeId={homeId} zones={zones} />}
    </section>
  );
}

export function OptimizationPage() {
  const { homeId } = useHome();
  const zones = useQuery({ queryKey: ['zones', homeId], queryFn: () => api.zones(homeId!), enabled: !!homeId });
  if (!homeId || zones.isLoading) return <Loading />;
  if (!zones.data) return <ErrorNote error={zones.error} />;
  return (
    <>
      <header className="page-head"><div><span className="eyebrow">Zelflerend klimaat</span><h1>Optimalisatie</h1></div></header>
      <section aria-labelledby="adapt-h" className="section">
        <h2 id="adapt-h">Adaptief model per zone</h2>
        {zones.data.length ? (
          <div className="grid-auto">{zones.data.map((z) => <ZoneAdaptive key={z.id} zone={z} />)}</div>
        ) : (
          <Empty>Nog geen klimaatzones.</Empty>
        )}
      </section>
      <Experiments homeId={homeId} zones={zones.data} />
    </>
  );
}
