import { useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type UploadProgress } from '../../api/client';
import { qk } from '../../api/keys';
import type { CompareStatus, RestoreCompare, RestoreJob, RestoreMode, RestoreReport, RestoreVerify } from '../../api/types';
import { ActionButton, ErrorNote, Loading, ProgressBar, yesNo } from '../../components/bits';
import { ago, bytes, dateTime } from '../../format';
import { COMPARE_STATUS_LABEL, KIND_LABEL, PROVIDER_LABEL, RESTORE_MODE_LABEL, SCHEMA_RELATION_LABEL, t } from '../../i18n';
import { clearRestore, loadRestore, saveRestore, useRestoreJob, type StoredRestore } from './restoreState';

const MODE_HELP: Record<RestoreMode, string> = {
  same_home:
    'Gebruik dit na een nieuwe NAS of een herinstallatie in hetzelfde huis. Alles komt terug zoals het was; apparaten worden herkend op hun identiteit.',
  new_home:
    'Gebruik dit na een verhuizing. De woning(en) uit de back-up worden gearchiveerd (geschiedenis blijft bewaard, koppelingen gaan uit) en er komt een nieuwe woning bij voor het nieuwe huis.',
};

function statusClass(s: CompareStatus | 'new'): string {
  return s === 'compatible' ? 'ok' : s === 'partial' ? 'warn' : s === 'new' ? 'info' : 'error';
}

/* ---------- step 1: upload + verify ---------- */

function UploadStep({ onVerified }: { onVerified: (v: RestoreVerify, passphrase: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [passphrase, setPassphrase] = useState('');
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const abort = useRef<AbortController | null>(null);
  const upload = useMutation({
    mutationFn: async () => {
      abort.current = new AbortController();
      setProgress({ loaded: 0, total: file!.size, fraction: 0 });
      return api.restoreVerify(file!, passphrase, { onProgress: setProgress, signal: abort.current.signal });
    },
    onSuccess: (v) => onVerified(v, passphrase),
    onSettled: () => {
      abort.current = null;
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (file && passphrase) upload.mutate();
  };
  const aborted = upload.error instanceof DOMException && upload.error.name === 'AbortError';
  return (
    <form className="stack" onSubmit={submit} aria-label="Back-up uploaden">
      <h3>1 · Back-up kiezen en controleren</h3>
      <p className="small muted" style={{ margin: 0 }}>
        Er verandert nog niets: de server controleert het bestand en de wachtwoordzin en laat zien wat erin zit.
      </p>
      <div className="form-grid">
        <label className="field">
          Back-upbestand (.shcbak)
          <input type="file" accept=".shcbak,application/octet-stream" aria-required="true" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={upload.isPending} />
        </label>
        <label className="field">
          Wachtwoordzin van de back-up
          <input type="password" autoComplete="off" required value={passphrase} onChange={(e) => setPassphrase(e.target.value)} disabled={upload.isPending} />
        </label>
        <ActionButton type="submit" className="btn primary" disabled={!file || !passphrase || upload.isPending}>
          {upload.isPending ? 'Bezig…' : 'Uploaden en controleren'}
        </ActionButton>
      </div>
      {upload.isPending && progress && (
        <div className="stack tight">
          <ProgressBar value={progress.fraction} label="Uploaden" />
          <div className="row small muted">
            <span role="status">
              {progress.fraction !== null && progress.fraction >= 1
                ? 'Geüpload; de server controleert het bestand…'
                : `${bytes(progress.loaded)} van ${bytes(progress.total)} geüpload`}
            </span>
            <button type="button" className="btn small" onClick={() => abort.current?.abort()}>Afbreken</button>
          </div>
        </div>
      )}
      {aborted && <div className="note info" role="status">Upload afgebroken.</div>}
      {upload.error && !aborted && <ErrorNote error={upload.error} />}
    </form>
  );
}

/* ---------- step 2: summary + choice A/B ---------- */

export function VerifySummary({ v }: { v: RestoreVerify }) {
  const perProvider = v.devices.reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.provider]: (acc[d.provider] ?? 0) + 1 }), {});
  return (
    <div className="stack">
      <dl className="facts">
        <div><dt>Bestand</dt><dd className="break">{v.filename ?? 'back-up'} · {bytes(v.size_bytes)}</dd></div>
        <div><dt>Gemaakt op</dt><dd>{dateTime(v.created_at)}</dd></div>
        <div><dt>Versie in de back-up</dt><dd>app {v.app_version ?? '?'} · schema {v.schema_revision ?? '?'}</dd></div>
        <div><dt>Deze server</dt><dd>app {v.current.app_version} · schema {v.current.schema_revision ?? '?'}</dd></div>
        <div><dt>Versievergelijking</dt><dd>{t(SCHEMA_RELATION_LABEL, v.schema_relation)}</dd></div>
        <div><dt>Records</dt><dd>{v.total_records.toLocaleString('nl-NL')} in {Object.keys(v.tables).length} tabellen</dd></div>
        <div><dt>Woningen</dt><dd>{v.homes.length ? v.homes.map((h) => `${h.name}${h.active ? '' : ' (gearchiveerd)'}`).join(', ') : 'geen'}</dd></div>
        <div><dt>Apparaten</dt><dd>{v.devices.length}{Object.keys(perProvider).length > 0 && ` (${Object.entries(perProvider).map(([p, n]) => `${t(PROVIDER_LABEL, p)} ${n}`).join(', ')})`}</dd></div>
        <div><dt>Apparaatsleutels</dt><dd>{v.credentials.total === 0 ? 'geen' : `${v.credentials.ok} van ${v.credentials.total} passen bij deze server`}</dd></div>
        <div><dt>Geheimen</dt><dd>{v.redacted ? 'nee (geredigeerde back-up)' : 'ja'}</dd></div>
        <div><dt>Zigbee2MQTT-gegevens</dt><dd>{yesNo(v.zigbee2mqtt_included)}</dd></div>
      </dl>
      {v.credentials.failed.length > 0 && (
        <div className="note warn" role="note">Sleutels die niet passen: {v.credentials.failed.join(', ')}.</div>
      )}
      {v.warnings.length > 0 && (
        <ul className="note warn plain-list" aria-label="Waarschuwingen">
          {v.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
      {!v.restorable && <div className="note error" role="alert">Deze back-up kan hier niet worden teruggezet: {v.refusal}</div>}
    </div>
  );
}

function ChooseStep({ v, passphrase, onStarted, onCancel }: { v: RestoreVerify; passphrase: string; onStarted: (r: StoredRestore) => void; onCancel: () => void }) {
  const [mode, setMode] = useState<RestoreMode>('same_home');
  const [sure, setSure] = useState(false);
  const apply = useMutation({
    mutationFn: () => api.restoreApply({ upload_id: v.upload_id, passphrase, mode }),
    onSuccess: (r) => {
      const stored: StoredRestore = { job_id: r.job_id, mode, started_at: Date.now(), filename: v.filename };
      saveRestore(stored);
      onStarted(stored);
    },
  });
  const cancel = useMutation({ mutationFn: () => api.restoreDiscard(v.upload_id), onSettled: onCancel });
  return (
    <div className="stack">
      <h3>2 · Controleren en kiezen</h3>
      <VerifySummary v={v} />
      {v.restorable && (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (sure) apply.mutate(); }} aria-label="Herstel starten">
          <fieldset className="choice">
            <legend>Hoe wil je terugzetten?</legend>
            {(['same_home', 'new_home'] as RestoreMode[]).map((m) => (
              <label key={m} className={`choice-card${mode === m ? ' selected' : ''}`}>
                <input type="radio" name="restore-mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
                <span>
                  <strong>{RESTORE_MODE_LABEL[m]}</strong>
                  <span className="small muted">{MODE_HELP[m]}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="check-row">
            <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} />
            Ik begrijp dat alle gegevens op deze server worden vervangen door de back-up, dat de app daarna in veilige modus staat en dat ik opnieuw moet inloggen.
          </label>
          <div className="row">
            <ActionButton type="submit" className="btn primary" disabled={!sure || apply.isPending}>{apply.isPending ? 'Starten…' : 'Herstellen'}</ActionButton>
            <button type="button" className="btn" disabled={apply.isPending || cancel.isPending} onClick={() => cancel.mutate()}>Annuleren</button>
          </div>
          {apply.error && <ErrorNote error={apply.error} />}
        </form>
      )}
      {!v.restorable && <div><button type="button" className="btn" onClick={() => cancel.mutate()}>Ander bestand kiezen</button></div>}
    </div>
  );
}

/* ---------- step 3: running / done ---------- */

/** What a restore could not (fully) take over, one line per table, as the CLI prints it (item 34):
 * tables this version no longer has, columns per table, and records the database update cleaned up. */
export function droppedLines(dropped: RestoreReport['dropped'] | null | undefined): string[] {
  const records = (n: number) => `${n.toLocaleString('nl-NL')} ${n === 1 ? 'record' : 'records'}`;
  const lines: string[] = [];
  for (const [table, n] of Object.entries(dropped?.tables ?? {})) {
    lines.push(`tabel ${table}: ${records(n)} (bestaat niet in deze versie)`);
  }
  for (const [table, cols] of Object.entries(dropped?.columns ?? {})) {
    const names = Array.isArray(cols) ? cols.map(String) : [];
    lines.push(`${table}: ${names.length === 1 ? 'kolom' : 'kolommen'} ${names.join(', ') || '?'} (${names.length === 1 ? 'bestaat' : 'bestaan'} niet in deze versie)`);
  }
  for (const [table, n] of Object.entries(dropped?.rows_removed_by_migration ?? {})) {
    lines.push(`${table}: ${records(n)} opgeruimd door de database-update`);
  }
  return lines;
}

function ReportSummary({ job }: { job: RestoreJob }) {
  const r = job.report;
  if (!r) return null;
  const lost = droppedLines(r.dropped);
  return (
    <dl className="facts">
      <div><dt>Records teruggezet</dt><dd>{r.total_records.toLocaleString('nl-NL')}</dd></div>
      <div><dt>Schema</dt><dd>{r.schema_from ?? '?'} → {r.schema_to ?? '?'}{r.migrated ? ' (bijgewerkt)' : ''}</dd></div>
      <div><dt>Keuze</dt><dd>{t(RESTORE_MODE_LABEL, r.mode)}</dd></div>
      <div><dt>Zigbee2MQTT-gegevens</dt><dd>{yesNo(r.zigbee2mqtt_included)}</dd></div>
      {r.archived_homes.length > 0 && <div><dt>Gearchiveerde woningen</dt><dd>{r.archived_homes.length}</dd></div>}
      {lost.length > 0 && (
        <div style={{ gridColumn: '1 / -1' }}>
          <dt>Niet (volledig) overgenomen</dt>
          <dd>
            <ul className="plain-list" style={{ display: 'flex', flexDirection: 'column', gap: 4, fontWeight: 500 }}>
              {lost.map((l) => <li key={l}>{l}</li>)}
            </ul>
          </dd>
        </div>
      )}
    </dl>
  );
}

function RunningStep({ stored, onReset }: { stored: StoredRestore; onReset: () => void }) {
  const qc = useQueryClient();
  const { job, missing, error } = useRestoreJob(stored.job_id);
  const state = missing ? 'done' : job?.state ?? 'running';
  return (
    <div className="stack" aria-live="polite">
      <h3>3 · Terugzetten ({t(RESTORE_MODE_LABEL, stored.mode)})</h3>
      {state === 'running' && (
        <>
          <p style={{ margin: 0 }}>Bezig: {job?.step ?? 'gestart'}. Dit duurt meestal een paar minuten. De achtergronddienst is zolang gepauzeerd.</p>
          <ProgressBar value={null} label="Herstel loopt" />
          <p className="small muted" style={{ margin: 0 }}>Je wordt daarna uitgelogd (inlogsessies zitten niet in een back-up). Deze pagina volgt het herstel ook op het inlogscherm.</p>
        </>
      )}
      {error && <ErrorNote error={error} />}
      {state === 'failed' && (
        <>
          <div className="note error" role="alert">Herstel mislukt: {job?.error ?? 'onbekende fout'}. Er is niets gewijzigd.</div>
          <div><button type="button" className="btn" onClick={() => { clearRestore(); onReset(); }}>Opnieuw beginnen</button></div>
        </>
      )}
      {state === 'done' && (
        <>
          <div className="note ok" role="status">Herstel klaar{job?.finished_at ? ` (${ago(job.finished_at)})` : ''}. De app staat in veilige modus: er gaan geen commando's naar apparaten.</div>
          {job && <ReportSummary job={job} />}
          <p className="small muted" style={{ margin: 0 }}>Log zo nodig opnieuw in. Hieronder zie je per apparaat en functie wat werkt.</p>
          <div><button type="button" className="btn" onClick={() => { qc.invalidateQueries({ queryKey: qk.auth }); qc.invalidateQueries({ queryKey: qk.restoreCompare }); }}>Verder</button></div>
        </>
      )}
    </div>
  );
}

/* ---------- step 4: compare + release ---------- */

export function CompareView({ data, onReleased }: { data: RestoreCompare; onReleased?: () => void }) {
  const qc = useQueryClient();
  const release = useMutation({
    mutationFn: api.releaseSafeMode,
    onSuccess: () => {
      clearRestore();
      qc.invalidateQueries({ queryKey: qk.system });
      qc.invalidateQueries({ queryKey: qk.settings });
      qc.invalidateQueries({ queryKey: qk.restoreCompare });
      qc.invalidateQueries({ queryKey: ['overview'] });
      onReleased?.();
    },
  });
  const s = data.summary;
  return (
    <div className="stack">
      <h3>4 · Vergelijken: wat werkt er?</h3>
      {data.restored_from && (
        <p className="small muted" style={{ margin: 0 }}>
          Teruggezet {dateTime(data.restored_from.restored_at)} uit een back-up van {dateTime(data.restored_from.created_at)} (app {data.restored_from.app_version ?? '?'}).
          Apparaten worden herkend op hun identiteit; wat de achtergronddienst sindsdien zag, telt.
        </p>
      )}
      <div className="row" role="list" aria-label="Samenvatting">
        <span role="listitem" className="pill ok">{COMPARE_STATUS_LABEL.compatible}: {s.compatible}</span>
        <span role="listitem" className="pill warn">{COMPARE_STATUS_LABEL.partial}: {s.partial}</span>
        <span role="listitem" className="pill error">{COMPARE_STATUS_LABEL.unavailable}: {s.unavailable}</span>
      </div>
      <h4>Functies</h4>
      <div className="table-wrap">
        <table className="stack-mobile">
          <thead><tr><th scope="col">Functie</th><th scope="col">Apparaat</th><th scope="col">Status</th><th scope="col">Toelichting</th></tr></thead>
          <tbody>
            {data.functions.map((f) => (
              <tr key={f.key}>
                <td data-label="Functie">{f.label}</td>
                <td data-label="Apparaat">{f.device_name ?? '–'}</td>
                <td data-label="Status"><span className={`pill ${statusClass(f.status)}`}>{t(COMPARE_STATUS_LABEL, f.status)}</span></td>
                <td data-label="Toelichting" className="small">{f.reason}{f.suggestion && <div className="muted">{f.suggestion}</div>}</td>
              </tr>
            ))}
            {data.functions.length === 0 && <tr><td colSpan={4} className="muted small">Geen functies gekoppeld.</td></tr>}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Alle apparaten ({data.devices.length})</summary>
        <div className="table-wrap">
          <table className="stack-mobile">
            <thead><tr><th scope="col">Apparaat</th><th scope="col">Soort</th><th scope="col">Status</th><th scope="col">Laatst gezien</th></tr></thead>
            <tbody>
              {data.devices.map((d) => (
                <tr key={d.id}>
                  <td data-label="Apparaat">{d.name}<div className="small muted">{t(PROVIDER_LABEL, d.provider)}</div></td>
                  <td data-label="Soort">{t(KIND_LABEL, d.kind)}</td>
                  <td data-label="Status"><span className={`pill ${statusClass(d.status)}`}>{t(COMPARE_STATUS_LABEL, d.status)}</span><div className="small muted">{d.reason}</div></td>
                  <td data-label="Laatst gezien">{ago(d.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <p className="small muted" style={{ margin: 0 }}>
        Niet beschikbaar? Zet het apparaat aan, wacht een paar minuten en vergelijk opnieuw, of koppel het opnieuw onder <Link to="/apparaten">Apparaten</Link>
        (bestaande geschiedenis blijft). Testen op Apparaten leest alleen uit en schakelt niets.
      </p>
      <div className="row">
        <button type="button" className="btn" onClick={() => qc.invalidateQueries({ queryKey: qk.restoreCompare })}>Opnieuw vergelijken</button>
        {data.safe_mode && (
          <ActionButton className="btn primary" disabled={release.isPending} onClick={() => { if (window.confirm('Werkt alles zoals verwacht? Na opheffen mag de app weer schakelen.')) release.mutate(); }}>
            Veilige modus opheffen
          </ActionButton>
        )}
      </div>
      {!data.safe_mode && <div className="note ok" role="status">Veilige modus is opgeheven; de app schakelt weer.</div>}
      {release.error && <ErrorNote error={release.error} />}
    </div>
  );
}

/* ---------- section ---------- */

type Step = { kind: 'upload' } | { kind: 'verified'; v: RestoreVerify; passphrase: string } | { kind: 'running'; stored: StoredRestore };

/** "Herstellen en verhuizen" on Systeem (the verhuisassistent, verbeterlijst 43). id="herstel". */
export function RestoreSection({ safeMode }: { safeMode: boolean }) {
  const [step, setStep] = useState<Step>(() => {
    const stored = loadRestore();
    return stored ? { kind: 'running', stored } : { kind: 'upload' };
  });
  const compare = useQuery({ queryKey: qk.restoreCompare, queryFn: () => api.restoreCompare(), enabled: safeMode || step.kind === 'running', retry: false });
  const cmp = compare.data && !Array.isArray(compare.data) && compare.data.restored_at ? compare.data : null;

  return (
    <section aria-labelledby="rs-h" className="card" id="herstel">
      <div>
        <span className="eyebrow">Verhuisassistent</span>
        <h2 id="rs-h">Herstellen en verhuizen</h2>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Zet een back-up terug op deze server: na een nieuwe NAS (A) of na een verhuizing naar een nieuw huis (B). Eerst wordt het bestand gecontroleerd,
        dan kies je A of B, en daarna vergelijk je per apparaat wat werkt voordat de app weer mag schakelen.
      </p>
      {step.kind === 'upload' && <UploadStep onVerified={(v, passphrase) => setStep({ kind: 'verified', v, passphrase })} />}
      {step.kind === 'verified' && (
        <ChooseStep v={step.v} passphrase={step.passphrase} onStarted={(stored) => setStep({ kind: 'running', stored })} onCancel={() => setStep({ kind: 'upload' })} />
      )}
      {step.kind === 'running' && <RunningStep stored={step.stored} onReset={() => setStep({ kind: 'upload' })} />}
      {compare.isLoading && (safeMode || step.kind === 'running') && <Loading label="Vergelijking laden…" />}
      {cmp && (cmp.safe_mode || step.kind === 'running') && (
        <CompareView data={cmp} onReleased={() => setStep({ kind: 'upload' })} />
      )}
    </section>
  );
}
