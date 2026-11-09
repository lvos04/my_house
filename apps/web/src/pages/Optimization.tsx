import { useId, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { AdaptiveModel, AdaptiveStatus, Experiment, ExperimentResult, Profile, Zone, ZoneRestored } from '../api/types';
import { useHome } from '../state';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';
import { CheckIcon } from '../components/Icons';
import { dateTime } from '../format';
import { EXPERIMENT_STATUS_LABEL, ZONE_RESTORED_TEXT, t } from '../i18n';
import { FormError, useClimateCan } from '../climate/common';
import { OutdoorSensorCard } from '../climate/OutdoorSensor';

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
  const { admin } = useClimateCan();
  const key = qk.adaptive(zone.id);
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
        <span className={`pill ${status.enabled ? 'ok' : 'neutral'}`}>{status.enabled ? 'Adaptief aan' : 'Adaptief uit'}</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Max. bijsturing ±{nl(status.max_offset_c)}°, harde grenzen blijven gelden, standaard uit.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div className="section-head small">
          <span>Baseline (passief meten)</span>
          {/* "Klaar" is geen gezondheid: inkt + vinkje, nooit groen (Peil §4.1 regel 1). */}
          <span className={b.ready ? 'inline-flex items-center gap-1 font-semibold' : 'muted'}>
            {b.ready && <CheckIcon />}
            {b.ready ? 'Klaar' : `${b.running_samples} van ${b.required} kwartieren`}
          </span>
        </div>
        <div className="band" role="progressbar" aria-label={`Baseline ${zone.name}`} aria-valuemin={0} aria-valuemax={b.required} aria-valuenow={Math.min(b.running_samples, b.required)}>
          <div className="band-fill" style={{ left: 0, width: `${pct}%`, background: 'var(--primary)' }} />
        </div>
        <span className="small muted">{b.samples} kwartieren gemeten{b.since ? ` sinds ${dateTime(b.since)}` : ''}; alleen kwartieren waarin de airco draait tellen mee.</span>
      </div>

      {status.models.length ? (
        <div className="table-wrap">
          <table className="stack-mobile">
            <thead><tr><th>Versie</th><th>R²</th><th>Reactie °C/u per °C</th><th>Bruikbaar</th><th>Status</th>{admin && <th><span className="sr-only">Actie</span></th>}</tr></thead>
            <tbody>
              {status.models.map((m) => (
                <tr key={m.id}>
                  <td data-label="Versie">v{m.version}{m.created_at ? <div className="small muted">{dateTime(m.created_at)}</div> : null}</td>
                  <td data-label="R²">{nl(m.params.r2, 2)}</td>
                  <td data-label="Reactie °C/u per °C">{nl(m.params.k_per_h, 2)}</td>
                  <td data-label="Bruikbaar">{m.params.usable ? 'ja' : <span className="muted">nee</span>}</td>
                  <td data-label="Status">{m.active ? <strong>actief</strong> : <span className="muted">–</span>}</td>
                  {admin && (
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
      {admin && (
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
  const q = useQuery({ queryKey: qk.adaptive(zone.id), queryFn: () => api.adaptive(zone.id), refetchInterval: 300_000 });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  return <AdaptiveCard zone={zone} status={q.data} />;
}

const VERDICT_TITLE: Record<ExperimentResult['verdict'], string> = {
  vergelijkbaar: 'Vergelijkbare meting',
  indicatief: 'Indicatieve vergelijking',
  onvoldoende: 'Onvoldoende gegevens',
};

/** Result of an experiment. Pure (no queries) so it can be shown anywhere. */
export function ExperimentResultView({ r }: { r: ExperimentResult }) {
  const comparable = r.verdict === 'vergelijkbaar';
  const pct = (v: number | null) => {
    if (v === null) return '–';
    if (comparable) return v < 0 ? `${nl(-v)}% besparing` : v > 0 ? `${nl(v)}% meer` : 'gelijk';
    return `${v > 0 ? '+' : v < 0 ? '−' : ''}${nl(Math.abs(v))}%`;
  };
  const otherZone = r.rows.some((row) => row.other_zone_mean_target_c !== null && row.other_zone_mean_target_c !== undefined);
  const notes = r.rows.flatMap((row) => (row.notes ?? []).map((n) => `${row.variant}: ${n}`));
  const diffLabel = comparable ? 't.o.v. eerste' : 'Verschil t.o.v. eerste (indicatief)';
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
          <table className="stack-mobile">
            <thead>
              <tr>
                <th>Variant</th><th>Dagen</th><th>kWh/dag</th>
                <th>{diffLabel}</th>
                <th>Comfort (binnen de band)</th>
                <th>Gem. afwijking °C</th><th>Starts</th><th>Buiten °C</th>
                {otherZone && <th>Andere zone, gem. doel °C</th>}
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row) => (
                <tr key={row.variant}>
                  <td data-label="Variant"><strong>{row.variant}</strong></td>
                  <td data-label="Dagen">{nl(row.days)}</td>
                  <td data-label="kWh/dag">{nl(row.kwh_per_day, 2)}</td>
                  <td data-label={diffLabel}>{pct(row.vs_first_pct)}</td>
                  <td data-label="Comfort (binnen de band)">{row.comfort_pct === null || row.comfort_pct === undefined ? '–' : `${nl(row.comfort_pct, 0)} % van de tijd`}</td>
                  <td data-label="Gem. afwijking °C">{nl(row.mean_abs_dev_c, 2)}</td>
                  <td data-label="Starts">{row.starts}</td>
                  <td data-label="Buiten °C">{nl(row.outdoor_mean_c)}</td>
                  {otherZone && <td data-label="Andere zone, gem. doel °C">{nl(row.other_zone_mean_target_c)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted" style={{ margin: 0 }}>
        Comfort: het deel van de gemeten tijd dat de kamer binnen de comfortband rond het doel van die variant bleef.
        {r.outdoor ? ` Buitensensor: ${r.outdoor.name}.` : ''}
      </p>
      {(r.confounders?.length ?? 0) > 0 && (
        <div className="note info">
          <strong>Hier kan de vergelijking scheef door gaan:</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{r.confounders.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
      {notes.length > 0 && (
        <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{notes.map((n) => <li key={n}>{n}</li>)}</ul>
      )}
    </div>
  );
}

function ExperimentResultLoader({ id }: { id: string }) {
  const q = useQuery({ queryKey: qk.experiment(id), queryFn: () => api.experimentResult(id) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  return <ExperimentResultView r={q.data} />;
}

interface VariantDraft { label: string; start: string; end: string; profileId: string }
const PRESETS = ['Constant', 'Nachtverlaging', 'Voorverwarmen'];

/** Client checks before POST (the API answers the same in Dutch, but this is quicker). */
export function experimentProblems(variants: VariantDraft[], autoApply: boolean): string[] {
  const out: string[] = [];
  const spans = variants.map((v) => ({ v, s: Date.parse(v.start), e: Date.parse(v.end) }));
  if (spans.some((x) => !Number.isFinite(x.s) || !Number.isFinite(x.e))) out.push('Vul bij elke variant een begin en einde in.');
  else if (spans.some((x) => x.e <= x.s)) out.push('Het einde van elke periode moet na het begin liggen.');
  if (autoApply) {
    if (variants.some((v) => !v.profileId)) out.push('Automatisch toepassen: kies voor elke variant een profiel.');
    const sorted = spans.filter((x) => Number.isFinite(x.s)).sort((a, b) => a.s - b.s);
    if (sorted.some((x, i) => i > 0 && sorted[i - 1].e > x.s)) out.push('Automatisch toepassen: de perioden mogen niet overlappen.');
  }
  return out;
}

function NewExperiment({ homeId, zones, profiles }: { homeId: string; zones: Zone[]; profiles: Profile[] }) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [zoneId, setZoneId] = useState(zones[0]?.id ?? '');
  const [autoApply, setAutoApply] = useState(false);
  const [variants, setVariants] = useState<VariantDraft[]>([
    { label: PRESETS[0], start: '', end: '', profileId: '' },
    { label: PRESETS[1], start: '', end: '', profileId: '' },
  ]);
  const [problems, setProblems] = useState<string[]>([]);
  const create = useMutation({
    mutationFn: () =>
      api.createExperiment(homeId, {
        name: name.trim(),
        zone_id: zoneId,
        auto_apply: autoApply,
        variants: variants.map((v) => ({ label: v.label.trim(), start: localInputToIso(v.start), end: localInputToIso(v.end), profile_id: v.profileId || null })),
      }),
    onSuccess: () => {
      setName('');
      qc.invalidateQueries({ queryKey: qk.experiments(homeId) });
    },
  });
  const upd = (i: number, patch: Partial<VariantDraft>) => setVariants(variants.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const p = experimentProblems(variants, autoApply);
    setProblems(p);
    if (!p.length) create.mutate();
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
        <fieldset key={i} className="c-variant">
          <legend>Variant {i + 1}</legend>
          <label className="field">Label<input required maxLength={50} value={v.label} onChange={(e) => upd(i, { label: e.target.value })} /></label>
          <label className="field">Begin<input type="datetime-local" required value={v.start} onChange={(e) => upd(i, { start: e.target.value })} /></label>
          <label className="field">Einde<input type="datetime-local" required value={v.end} onChange={(e) => upd(i, { end: e.target.value })} /></label>
          <label className="field">Profiel
            <select value={v.profileId} onChange={(e) => upd(i, { profileId: e.target.value })}>
              <option value="">{autoApply ? 'Kies een profiel…' : 'Geen (alleen meten)'}</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {variants.length > 2 && <button type="button" className="btn" aria-label={`Variant ${i + 1} verwijderen`} onClick={() => setVariants(variants.filter((_, j) => j !== i))}>Verwijderen</button>}
        </fieldset>
      ))}
      <label className="check-row">
        <input type="checkbox" checked={autoApply} onChange={(e) => setAutoApply(e.target.checked)} aria-describedby="auto-help" />
        Profielen automatisch toepassen
      </label>
      <span id="auto-help" className="small muted">
        De regeling zet in elke periode het profiel van die variant op de zone. Dan heeft elke variant een profiel nodig en mogen de perioden niet overlappen. Bedien je de zone tussendoor met de hand of zet je hem uit, dan pauzeert het experiment.
      </span>
      {profiles.length === 0 && <span className="small muted">Nog geen comfortprofielen; maak ze aan op Klimaat › Comfortprofielen.</span>}
      <div className="row" style={{ gap: 8 }}>
        {variants.length < 4 && (
          <button type="button" className="btn" onClick={() => setVariants([...variants, { label: PRESETS[variants.length] ?? `Variant ${variants.length + 1}`, start: '', end: '', profileId: '' }])}>Variant toevoegen</button>
        )}
        <ActionButton type="submit" className="btn primary" disabled={create.isPending || !zoneId}>Experiment aanmaken</ActionButton>
      </div>
      {problems.length > 0 && (
        <div className="error-text" role="alert">{problems.length === 1 ? problems[0] : <ul className="plain-list">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}</div>
      )}
      <FormError error={create.error} />
    </form>
  );
}

// Experimentstatus: neutrale labels; gepauzeerd = info (pauze ≠ beperkt, loopt ≠ gezond; PEIL_MIGRATIE §6).
const STATUS_PILL: Record<string, string> = { defined: 'neutral', running: 'ok', paused: 'info', finished: 'neutral', stopped: 'neutral' };

/** Stopping or deleting an auto-apply experiment that still runs: by default the zone gets back the
 * profile and schedule it had before the experiment (restore); or it keeps what it has now. */
function LetGoChoice({ action, name, pending, onConfirm, onCancel }: {
  action: 'stop' | 'delete';
  name: string;
  pending: boolean;
  onConfirm: (restore: boolean) => void;
  onCancel: () => void;
}) {
  const [restore, setRestore] = useState(true);
  const id = useId();
  const title = `Experiment ${name} ${action === 'stop' ? 'stoppen' : 'verwijderen'}`;
  return (
    <div className="note info stack" role="group" aria-labelledby={`${id}-h`}>
      <strong id={`${id}-h`}>{title}</strong>
      {action === 'delete' && <span className="small">De resultaten zijn daarna weg.</span>}
      <fieldset className="choice">
        <legend>Wat moet de zone daarna doen?</legend>
        <label className={`choice-card${restore ? ' selected' : ''}`}>
          <input type="radio" name={`${id}-zone`} checked={restore} onChange={() => setRestore(true)} />
          <span>
            <strong>Terugzetten</strong>
            <span className="small muted">Het profiel en schema van vóór het experiment komen terug.</span>
          </span>
        </label>
        <label className={`choice-card${!restore ? ' selected' : ''}`}>
          <input type="radio" name={`${id}-zone`} checked={!restore} onChange={() => setRestore(false)} />
          <span>
            <strong>Houden</strong>
            <span className="small muted">De zone houdt het profiel en schema dat hij nu heeft (die van het experiment).</span>
          </span>
        </label>
      </fieldset>
      <span className="small muted">Kreeg de zone intussen met de hand een ander profiel, dan blijft dat in beide gevallen staan.</span>
      <div className="c-actions">
        <ActionButton className={`btn ${action === 'delete' ? 'danger' : 'primary'}`} disabled={pending} onClick={() => onConfirm(restore)}>{title}</ActionButton>
        <button type="button" className="btn" onClick={onCancel}>Annuleren</button>
      </div>
    </div>
  );
}

function ExperimentCard({ e, zones, profiles, homeId }: { e: Experiment; zones: Zone[]; profiles: Profile[]; homeId: string }) {
  const qc = useQueryClient();
  const { admin } = useClimateCan();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<'stop' | 'delete' | null>(null);
  const [outcome, setOutcome] = useState<ZoneRestored | null>(null);
  const done = () => {
    qc.invalidateQueries({ queryKey: qk.experiments(homeId) });
    qc.invalidateQueries({ queryKey: qk.experiment(e.id) });
  };
  /** Stop/delete may have put the zone's old profile and schedule back (item 45). */
  const zoneChanged = () => {
    done();
    qc.invalidateQueries({ queryKey: qk.profiles(homeId) });
    qc.invalidateQueries({ queryKey: qk.zones(homeId) });
    qc.invalidateQueries({ queryKey: qk.overview(homeId) });
    if (e.zone_id) {
      qc.invalidateQueries({ queryKey: qk.zone(e.zone_id) });
      qc.invalidateQueries({ queryKey: qk.schedule(e.zone_id) });
      qc.invalidateQueries({ queryKey: qk.zoneLog(e.zone_id) });
    }
  };
  const pause = useMutation({ mutationFn: () => api.pauseExperiment(e.id), onSuccess: done });
  const resume = useMutation({ mutationFn: () => api.resumeExperiment(e.id), onSuccess: done });
  const stop = useMutation({
    mutationFn: (restore: boolean) => api.stopExperiment(e.id, { restore }),
    onSuccess: (r) => {
      setConfirming(null);
      setOutcome(r?.zone_restored ?? null);
      zoneChanged();
    },
  });
  const remove = useMutation({
    mutationFn: (restore: boolean) => api.deleteExperiment(e.id, { restore }),
    onSuccess: () => {
      setConfirming(null);
      zoneChanged();
    },
  });
  const err = pause.error ?? resume.error ?? stop.error ?? remove.error;
  const zone = zones.find((z) => z.id === e.zone_id);
  const profileName = (id?: string | null) => (id ? profiles.find((p) => p.id === id)?.name ?? 'onbekend profiel' : null);
  const active = e.status === 'defined' || e.status === 'running' || e.status === 'paused';
  const current = e.current_variant !== null && e.current_variant !== undefined ? e.variants[e.current_variant] : null;
  // Only an auto-apply experiment that still runs can have its profile on the zone; a finished one
  // already gave the zone back (the worker restores at the end), one that only measures never touched it.
  const choice = e.auto_apply && active;
  const askStop = () => {
    if (choice) return setConfirming(confirming === 'stop' ? null : 'stop');
    if (window.confirm(`Experiment ${e.name} stoppen? Dit experiment meet alleen; de zone verandert niet.`)) stop.mutate(true);
  };
  const askDelete = () => {
    if (choice) return setConfirming(confirming === 'delete' ? null : 'delete');
    if (window.confirm(`Experiment ${e.name} verwijderen? De resultaten zijn daarna weg.`)) remove.mutate(true);
  };

  return (
    <article className="card" aria-labelledby={`exp-${e.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <div>
          <h3 id={`exp-${e.id}`}>{e.name}</h3>
          <span className="card-sub">{zone ? `${zone.name} · ` : ''}{e.variants.map((v) => v.label).join(' vs ')}</span>
        </div>
        <div className="c-pills">
          <span className={`pill ${STATUS_PILL[e.status] ?? 'neutral'}`}>{t(EXPERIMENT_STATUS_LABEL, e.status)}</span>
          {e.auto_apply && <span className="pill info">Profielen automatisch</span>}
        </div>
      </div>
      {current && e.status === 'running' && <span className="small">Nu actief: <strong>{current.label}</strong>{profileName(current.profile_id) ? ` (profiel ${profileName(current.profile_id)})` : ''}</span>}
      {e.status === 'paused' && (
        <div className="note warn">
          Gepauzeerd{e.paused_reason ? `: ${e.paused_reason}` : '.'} {admin ? 'Hervat het experiment zodra de zone weer het schema volgt.' : ''}
        </div>
      )}
      {outcome && <div className="note info" role="status">Experiment gestopt. {t(ZONE_RESTORED_TEXT, outcome)}</div>}
      <ul className="c-list">
        {e.variants.map((v, i) => (
          <li key={i}>
            <strong>{v.label}</strong>: {dateTime(v.start)} – {dateTime(v.end)}
            {' · '}{profileName(v.profile_id) ? `profiel ${profileName(v.profile_id)}` : 'geen profiel'}
          </li>
        ))}
      </ul>
      <div className="c-actions">
        <button type="button" className="btn" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Resultaat verbergen' : 'Resultaat'}</button>
        {admin && (e.status === 'running' || e.status === 'defined') && (
          <ActionButton className="btn" disabled={pause.isPending} onClick={() => pause.mutate()}>Pauzeren</ActionButton>
        )}
        {admin && e.status === 'paused' && (
          <ActionButton className="btn primary" disabled={resume.isPending} onClick={() => resume.mutate()}>Hervatten</ActionButton>
        )}
        {admin && active && (
          <ActionButton className="btn" disabled={stop.isPending} aria-expanded={choice ? confirming === 'stop' : undefined} onClick={askStop}>
            Stoppen
          </ActionButton>
        )}
        {admin && (
          <ActionButton className="btn danger" disabled={remove.isPending} aria-expanded={choice ? confirming === 'delete' : undefined} onClick={askDelete}>
            Verwijderen
          </ActionButton>
        )}
      </div>
      {admin && choice && confirming && (
        <LetGoChoice
          key={confirming}
          action={confirming}
          name={e.name}
          pending={confirming === 'stop' ? stop.isPending : remove.isPending}
          onConfirm={(restore) => (confirming === 'stop' ? stop.mutate(restore) : remove.mutate(restore))}
          onCancel={() => setConfirming(null)}
        />
      )}
      <FormError error={err} />
      {open && <ExperimentResultLoader id={e.id} />}
    </article>
  );
}

function Experiments({ homeId, zones }: { homeId: string; zones: Zone[] }) {
  const { admin } = useClimateCan();
  const list = useQuery({ queryKey: qk.experiments(homeId), queryFn: () => api.experiments(homeId) });
  const profiles = useQuery({ queryKey: qk.profiles(homeId), queryFn: () => api.profiles(homeId) });
  const plist = Array.isArray(profiles.data) ? profiles.data : [];
  return (
    <section aria-labelledby="exp-h" className="section">
      <h2 id="exp-h">Experimenten</h2>
      <p className="small muted" style={{ margin: 0 }}>We noemen het alleen een besparing als de perioden lang genoeg zijn, de airco apart gemeten is en het buiten ongeveer even koud was.</p>
      {list.isLoading && <Loading />}
      {list.error && <ErrorNote error={list.error} />}
      {list.data?.length === 0 && <Empty>Nog geen experimenten.</Empty>}
      {list.data?.map((e) => <ExperimentCard key={e.id} e={e} zones={zones} profiles={plist} homeId={homeId} />)}
      {admin && zones.length > 0 && <NewExperiment homeId={homeId} zones={zones} profiles={plist} />}
    </section>
  );
}

export function OptimizationPage() {
  const { homeId } = useHome();
  const zones = useQuery({ queryKey: qk.zones(homeId ?? ''), queryFn: () => api.zones(homeId!), enabled: !!homeId });
  if (!homeId || zones.isLoading) return <Loading />;
  if (!zones.data) return <ErrorNote error={zones.error} />;
  return (
    <>
      <header className="page-head"><div><span className="eyebrow">Zelflerend klimaat</span><h1>Optimalisatie</h1></div></header>
      <section aria-labelledby="adapt-h" className="section">
        <h2 id="adapt-h">Adaptief model per zone</h2>
        {zones.data.length ? (
          <div className="c-grid">{zones.data.map((z) => <ZoneAdaptive key={z.id} zone={z} />)}</div>
        ) : (
          <Empty>Nog geen klimaatzones.</Empty>
        )}
      </section>
      <section aria-labelledby="buiten-h" className="section">
        <h2 id="buiten-h">Weer meewegen</h2>
        <OutdoorSensorCard homeId={homeId} />
      </section>
      <Experiments homeId={homeId} zones={zones.data} />
    </>
  );
}
