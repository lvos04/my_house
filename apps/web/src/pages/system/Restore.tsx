import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type UploadProgress } from '../../api/client';
import { qk } from '../../api/keys';
import type { CompareStatus, RestoreCompare, RestoreDrill, RestoreJob, RestoreMode, RestorePreview, RestoreRekey, RestoreReport, RestoreVerify, VerifiedDatabaseDump } from '../../api/types';
import { ActionButton, ErrorNote, Loading, ProgressBar, yesNo } from '../../components/bits';
import { ago, bytes, dateTime } from '../../format';
import { COMPARE_STATUS_LABEL, KIND_LABEL, PROVIDER_LABEL, RESTORE_MODE_LABEL, SCHEMA_RELATION_LABEL, t } from '../../i18n';
import { clearRestore, loadRestore, saveRestore, useRestoreJob, type StoredRestore } from './restoreState';
import { DatabaseDumpHerstel } from './DatabaseDumpHerstel';
import { ZigbeeHerstelkopie } from './ZigbeeHerstelkopie';

const MODE_HELP: Record<RestoreMode, string> = {
  same_home:
    'Zet de gegevens voor dezelfde woning terug op deze installatie.',
  new_home:
    'Gebruik dit na een verhuizing. De woning(en) uit de back-up worden gearchiveerd (geschiedenis blijft bewaard, koppelingen gaan uit) en er komt een nieuwe woning bij voor het nieuwe huis.',
  migration: 'Gebruik een nieuwe app met eigen opslag. Voer eerst de herstelproef uit.',
};

function statusClass(s: CompareStatus | 'new'): string {
  return s === 'compatible' ? 'ok' : s === 'partial' ? 'warn' : s === 'new' ? 'info' : 'unknown';
}

/* ---------- step 1: upload + verify ---------- */

function UploadStep({ onVerified }: { onVerified: (v: RestoreVerify | VerifiedDatabaseDump, passphrase: string) => void }) {
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
          Back-upbestand (.shcbak of .shcdump)
          <input type="file" accept=".shcbak,.shcdump,application/octet-stream" aria-required="true" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={upload.isPending} />
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
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const drill = useMutation({ mutationFn: () => api.restoreDrill(v.upload_id, passphrase) });
  const drillId = drill.data?.drill_id;
  const migration = mode === 'migration';
  const input = { upload_id: v.upload_id, passphrase, mode, ...(migration && drillId ? { drill_id: drillId } : {}) };
  const preview = useMutation({ mutationFn: () => api.restorePreview(input) });
  const reviewed = preview.data?.comparison.mode === mode ? preview.data : undefined;
  const canApply = sure && reviewed && !preview.isPending && !drill.isPending && (!migration || Boolean(drillId));
  const apply = useMutation({
    mutationFn: () => api.restoreApply({ ...input, rollback_id: reviewed!.comparison.rollback_id, confirmation: reviewed!.confirmation }),
    onSuccess: (r) => {
      const stored: StoredRestore = { job_id: r.job_id, mode, started_at: Date.now(), filename: v.filename };
      saveRestore(stored);
      onStarted(stored);
    },
  });
  const cancel = useMutation({ mutationFn: () => api.restoreDiscard(v.upload_id), onSettled: onCancel });
  return (
    <div className="stack">
      <h3 ref={heading} tabIndex={-1}>2 · Controleren en kiezen</h3>
      <VerifySummary v={v} />
      {v.restorable && (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (canApply && !apply.isPending) apply.mutate(); }} aria-label="Herstel starten">
          <fieldset className="choice" disabled={apply.isPending || preview.isPending || drill.isPending}>
            <legend>Hoe wil je terugzetten?</legend>
            {(['same_home', 'new_home', 'migration'] as const).map((m) => (
              <label key={m} className={`choice-card${mode === m ? ' selected' : ''}`}>
                <input type="radio" name="restore-mode" value={m} checked={mode === m} onChange={() => { setMode(m); setSure(false); preview.reset(); drill.reset(); }} />
                <span>
                  <strong>{RESTORE_MODE_LABEL[m]}</strong>
                  <span className="small muted">{MODE_HELP[m]}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <div><ActionButton className="btn" disabled={drill.isPending || apply.isPending || preview.isPending} onClick={() => { setSure(false); preview.reset(); drill.mutate(); }}>Herstelproef uitvoeren</ActionButton></div>
          {drill.isPending && <ProgressBar value={null} label="Herstelproef uitvoeren…" />}
          {drill.error && <ErrorNote error={drill.error} />}
          {drill.data && <DrillReport data={drill.data} migration={migration} />}
          <div><ActionButton className="btn" disabled={apply.isPending || preview.isPending || drill.isPending || (migration && !drillId)}
            onClick={() => { setSure(false); preview.reset(); preview.mutate(); }}>Doelback-up maken en vergelijken</ActionButton></div>
          {preview.isPending && <ProgressBar value={null} label="Doelback-up verifiëren…" />}
          {preview.error && <ErrorNote error={preview.error} />}
          {reviewed && <BeforeRestore data={reviewed.comparison} />}
          <label className="check-row">
            <input type="checkbox" checked={sure} disabled={!reviewed || preview.isPending} onChange={(e) => setSure(e.target.checked)} />
            {migration ? 'Ik bevestig deze vergelijking en doelback-up. Het tijdelijke beheerdersaccount wordt vervangen.' : 'Ik begrijp dat alle gegevens op deze server worden vervangen door de back-up, dat de app daarna in veilige modus staat en dat ik opnieuw moet inloggen.'}
          </label>
          <div className="row">
            <ActionButton type="submit" className="btn primary" disabled={!canApply || apply.isPending || cancel.isPending}>{apply.isPending ? 'Starten…' : migration ? 'Nieuwe app vullen' : 'Herstellen'}</ActionButton>
            <button type="button" className="btn" disabled={apply.isPending || drill.isPending || cancel.isPending} onClick={() => cancel.mutate()}>Annuleren</button>
          </div>
          {apply.error && <ErrorNote error={apply.error} />}
        </form>
      )}
      {!v.restorable && <div><button type="button" className="btn" onClick={() => cancel.mutate()}>Ander bestand kiezen</button></div>}
    </div>
  );
}

function BeforeRestore({ data }: { data: RestorePreview['comparison'] }) {
  return <div className="stack">
    <h4>Vergelijking vóór terugzetten</h4>
    <p>Doelwoningen: {data.homes_before.map(h => h.name).join(', ') || 'Geen'}. Woningen in het archief: {data.homes_after.map(h => h.name).join(', ') || 'Geen'}.</p>
    <p><a href={api.restoreFileUrl(data.rollback_id)} download>Geverifieerde doelback-up downloaden</a>. Bewaar deze met de wachtwoordzin van het gekozen archief.</p>
    <table className="stack-mobile"><thead><tr><th scope="col">Tabel</th><th scope="col">Op het doel</th><th scope="col">In het archief</th><th scope="col">Inhoud</th></tr></thead>
      <tbody>{Object.entries(data.tables).map(([name, row]) => <tr key={name}>
        <td data-label="Tabel">{name}</td><td data-label="Op het doel">{row.before}</td>
        <td data-label="In het archief">{row.after}</td><td data-label="Inhoud">{row.same ? 'Gelijk' : 'Verschil'}</td>
      </tr>)}</tbody></table>
  </div>;
}

function SourcePauseForm() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: qk.settings, queryFn: api.settings });
  const [phrase, setPhrase] = useState('');
  const refresh = () => qc.invalidateQueries({ queryKey: qk.settings });
  const pause = useMutation({ mutationFn: api.pauseSource, onSuccess: refresh });
  const resume = useMutation({ mutationFn: api.resumeSource, onSuccess: () => { exported.reset(); return refresh(); } });
  const exported = useMutation({
    mutationFn: (stop: boolean) => api.sourceExport(phrase, stop),
    onSuccess: () => setPhrase(''),
    onSettled: refresh,
  });
  const proof = settings.data?.source_pause;
  const fileId = exported.data?.file_id ?? proof?.file_id ?? proof?.interim_file_id;
  const last = settings.data?.last_restore;
  return <>{last && <p>Laatste hersteltaak ({dateTime(last.started_at)}): {last.step}.{' '}
    <a href={api.restoreFileUrl(last.rollback_id)} download>Doelback-up van deze taak downloaden</a>.</p>}
    <details className="stack"><summary>Definitieve export op de bron</summary>
    {settings.isLoading ? <Loading label="Bronstatus laden…" /> : settings.error ? <ErrorNote error={settings.error} /> : proof ? <>
      <p role="status">Bron gepauzeerd. Laatste meetrecord: {proof.last_reading ?? 'Geen'}. Aantal metingen: {proof.reading_count}. Het overstapbewijs vervalt {dateTime(proof.expires)}.</p>
      <p>{proof.stopped_at ? `Bronregistratie afgesloten op ${dateTime(proof.stopped_at)}.`
        : 'Aanmelden blijft mogelijk tot je de bronregistratie afsluit. De definitieve export bevat ook de laatste aanmeldingen.'}</p>
      <form className="stack" onSubmit={e => { e.preventDefault(); exported.mutate(true); }}>
        <label className="field">Wachtwoordzin voor de export<input type="password" minLength={12} required value={phrase} onChange={e => setPhrase(e.target.value)} /></label>
        <div className="row"><ActionButton type="submit" className="btn" disabled={phrase.length < 12 || exported.isPending}>
          {proof.stopped_at ? 'Definitieve export opnieuw maken' : 'Bronregistratie afsluiten en exporteren'}</ActionButton>
          {!proof.stopped_at && <ActionButton type="button" className="btn" disabled={phrase.length < 12 || exported.isPending}
            onClick={() => exported.mutate(false)}>Tussenexport maken</ActionButton>}
        </div>
      </form>
      {exported.isPending && <ProgressBar value={null} label="Bronexport controleren…" />}
      {fileId && <div><a className="btn" href={api.restoreFileUrl(fileId)} download>
        {(exported.data ? exported.data.source_pause.stopped_at : proof.file_id)
          ? 'Definitieve export downloaden' : 'Tussenexport downloaden'}</a></div>}
      <p>{proof.stopped_at === null ? 'Je kunt de bron nu hervatten.' : <>
        Bewaar de doelexport en beide opslagen en laat de oude worker uit. Volg de installatiehandleiding om de offline
        bronkopie met de huidige <code>shc-api prepare-connector-fallback</code> voor te bereiden.
        Gebruik het Terugval-ID van die kopie. Stop de doelapp en hervat de bron op die kopie pas na exitcode 0
        en na de vervaltijd. Bij gewijzigde opslag kun je op de oorspronkelijke opslag hervatten met de worker
        gestopt. Maak daarna een nieuwe kopie en bereid die voor.
      </>}</p>
      <div><ActionButton className="btn" disabled={resume.isPending} onClick={() => resume.mutate()}>Bron hervatten</ActionButton></div>
    </> : <>
      <p>Pauzeer de bron voor de overstap. Bestaande sessies kunnen blijven lezen. Nieuwe aanmeldingen blijven mogelijk tot je de bronregistratie afsluit. Het bewijs vervalt één uur na het begin van de pauze.</p>
      <div><ActionButton className="btn" disabled={pause.isPending} onClick={() => pause.mutate()}>Bron pauzeren voor overstap</ActionButton></div>
    </>}
    {(pause.error || resume.error || exported.error) && <ErrorNote error={pause.error || resume.error || exported.error} />}
  </details></>;
}

function DrillReport({ data, migration }: { data: RestoreDrill; migration: boolean }) {
  return (
    <div className="stack">
      <h4>Herstelproef in tijdelijke database</h4>
      <p role="status">{data.data_ok ? 'De tabellen zijn gecontroleerd.' : 'Herstelproef onvolledig. Bekijk de verschillen.'} Apparaatsleutels opnieuw inlezen of koppelen: {data.credentials.failed.length}.</p>
      {migration && <dl className="facts">
        <div><dt>Broninstallatie</dt><dd className="break-all">{data.source_id ?? 'Onbekend'}</dd></div>
        <div><dt>Doelinstallatie</dt><dd className="break-all">{data.target_id}</dd></div>
      </dl>}
      {migration && data.migration_refusal && <div className="note error" role="alert">{data.migration_refusal}</div>}
      <details>
        <summary>Resultaat per tabel</summary>
        <table className="stack-mobile">
          <thead><tr><th scope="col">Tabel</th><th scope="col">Back-up</th><th scope="col">Teruggezet</th><th scope="col">Gecontroleerd</th><th scope="col">Status</th></tr></thead>
          <tbody>{Object.entries(data.tables).map(([name, counts]) => (
            <tr key={name}>
              <td data-label="Tabel">{name}</td><td data-label="Back-up">{counts.backup}</td>
              <td data-label="Teruggezet">{counts.restored ?? 'Onbekend'}</td><td data-label="Gecontroleerd">{counts.now ?? 'Onbekend'}</td>
              <td data-label="Status">{counts.ok ? 'Komt overeen' : 'Verschil'}</td>
            </tr>
          ))}</tbody>
        </table>
        {droppedLines(data.report.dropped).map((line) => <p key={line}>{line}</p>)}
      </details>
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
  const { job, state, error } = useRestoreJob(stored.job_id);
  return (
    <div className="stack" aria-live="polite">
      <h3>3 · Terugzetten ({t(RESTORE_MODE_LABEL, stored.mode)})</h3>
      {state === 'loading' && <Loading label="Taakstatus opvragen…" />}
      {state === 'running' && (
        <>
          <p style={{ margin: 0 }}>Bezig: {job?.step ?? 'gestart'}. Dit duurt meestal een paar minuten. De achtergronddienst is zolang gepauzeerd.</p>
          <ProgressBar value={null} label="Herstel loopt" />
          <p className="small muted" style={{ margin: 0 }}>Je wordt daarna uitgelogd (inlogsessies zitten niet in een back-up). Deze pagina volgt het herstel ook op het inlogscherm.</p>
        </>
      )}
      {state === 'unknown' && <div className="note warn" role="status">Onbekende uitkomst. Meld je opnieuw aan en controleer de taak en de herkomst van de doelgegevens.
        <button type="button" className="btn" onClick={() => { clearRestore(); onReset(); }}>Deze taak sluiten</button></div>}
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

function RekeyForm() {
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<RestoreRekey | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const input = fileInput.current;
    const file = input?.files?.[0];
    if (!input || !file || busy) return;
    input.value = '';
    setError(null);
    setResult(null);
    if (!file.size || file.size > 4096) {
      setError(new Error('Kies een sleutelbestand van maximaal 4 KiB.'));
      return;
    }
    setBusy(true);
    try {
      setResult(await api.restoreRekey(file));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} aria-labelledby="rekey-heading">
      <h3 id="rekey-heading">Apparaatsleutels herstellen</h3>
      <p className="small muted">Gebruik het sleutelbestand van de broninstallatie als de teruggezette koppelingen om de oude hoofdsleutel vragen.</p>
      <label className="field">Bestand met de oude hoofdsleutel<input ref={fileInput} type="file" required disabled={busy} /></label>
      <div><ActionButton className="btn" type="submit" disabled={busy}>Oude hoofdsleutel uploaden</ActionButton></div>
      {busy && <ProgressBar value={null} label="Sleutels opnieuw versleutelen…" />}
      {error != null && <ErrorNote error={error} />}
      {result && <p role="status">Opnieuw versleuteld: {result.rekeyed}. Al bruikbaar: {result.already_current}. Opnieuw te koppelen: {result.failed}. Opnieuw in te stellen: {result.settings_failed}.</p>}
    </form>
  );
}

function MigrationApproval({ migration, pending, onConfirm }: {
  migration: NonNullable<RestoreCompare['migration']>;
  pending: boolean;
  onConfirm: (confirmation: string) => void;
}) {
  const [sure, setSure] = useState(false);
  const confirmation = migration.confirmation;
  return (
    <div className="stack">
      <dl className="facts">
        <div><dt>Broninstallatie</dt><dd className="break-all">{migration.source_id}</dd></div>
        <div><dt>Doelinstallatie</dt><dd className="break-all">{migration.target_id}</dd></div>
      </dl>
      {confirmation ? (
        <form className="stack" aria-label="Overstap bevestigen" onSubmit={(event) => {
          event.preventDefault();
          if (sure && !pending) onConfirm(confirmation);
        }}>
          <label className="check-row"><input type="checkbox" checked={sure} onChange={(event) => setSure(event.target.checked)} />
            Ik heb de vergelijking bekeken, vastgesteld dat de laatste gegevens zijn meegenomen en de broninstallatie stilgezet.
          </label>
          <div><ActionButton type="submit" className="btn primary" disabled={!sure || pending}>Overstap bevestigen</ActionButton></div>
        </form>
      ) : <div className="note warn" role="status">Overstap geblokkeerd. {migration.refusal ?? (migration.source_complete === false ? 'Het bronbewijs ontbreekt of is verlopen.' : 'Controleer de ontbrekende of onzekere apparaten en functies. Vergelijk opnieuw.')}</div>}
    </div>
  );
}

const inventoryNames: Record<string, string> = {
  users: 'Gebruikers', api_clients: 'Gekoppelde clients', homes: 'Woningen', rooms: 'Kamers',
  integrations: 'Integraties', devices: 'Apparaten', logical_roles: 'Functies', device_bindings: 'Apparaatbindingen',
  button_mappings: 'Knoppen, gebeurtenissen en acties', climate_zones: 'Klimaatzones en regeling',
  comfort_profiles: 'Comfortprofielen', schedule_blocks: "Tijdschema’s", adaptive_models: 'Adaptieve modellen',
  experiments: 'Experimenten en automatische toepassing', tariffs: 'Tarieven', settings: 'Instellingen',
  commands: 'Opdrachten', connector_changes: 'Connectorherstel',
  sensor_readings: 'Metingen', energy_hourly: 'Energie per uur', zone_log: 'Zonelogboek',
  audit_log: 'Beheerlogboek', alerts: 'Meldingen', backups: 'Back-uphistorie',
};

export function CompareView({ data, onReleased }: { data: RestoreCompare; onReleased?: () => void }) {
  const qc = useQueryClient();
  const release = useMutation({
    mutationFn: (confirmation?: string) => confirmation ? api.confirmMigration(confirmation) : api.releaseSafeMode(),
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
      {data.source_homes && data.homes && <dl className="facts">
        <div><dt>Bestaande woning</dt><dd>{Object.values(data.source_homes).join(', ') || 'Onbekend'}</dd></div>
        <div><dt>Nieuwe woning</dt><dd>{Object.values(data.homes).join(', ') || 'Onbekend'}</dd></div>
      </dl>}
      <div className="row" role="list" aria-label="Samenvatting">
        <span role="listitem" className="pill ok">{COMPARE_STATUS_LABEL.compatible}: {s.compatible}</span>
        <span role="listitem" className="pill warn">{COMPARE_STATUS_LABEL.partial}: {s.partial}</span>
        <span role="listitem" className="pill unknown">{COMPARE_STATUS_LABEL.unavailable}: {s.unavailable}</span>
      </div>
      {data.duplicates && (
        <div className="stack">
          <h4>Dubbele apparaten</h4>
          {data.duplicates.length ? <ul className="meter-list" aria-label="Dubbele apparaten">
            {data.duplicates.map((group) => (
              <li key={group.devices.map((d) => d.id).join(':')}>
                <div><strong>Dubbel apparaat</strong> · {t(PROVIDER_LABEL, group.provider)}
                  <ul>{group.devices.map((d) => <li key={d.id}>{d.name} · {d.home_name ?? 'Onbekende woning'}</li>)}</ul>
                </div>
              </li>
            ))}
          </ul> : <p>Geen dubbele apparaten gevonden.</p>}
          {!!data.missing_devices && <p className="note warn">Ontbrekende apparaten: {data.missing_devices}.</p>}
        </div>
      )}
      {data.inventory && <details open={data.commissioning_gate?.same === false || Object.values(data.inventory).some(item => !item.same)}>
        <summary>Inhoud van de bronexport en het doel</summary>
        <div className="table-wrap"><table className="stack-mobile">
          <thead><tr><th scope="col">Gegevens</th><th scope="col">Bron</th><th scope="col">Doel</th><th scope="col">Inhoud</th></tr></thead>
          <tbody>{Object.entries(data.inventory).map(([kind, item]) => <tr key={kind}>
            <td data-label="Gegevens">{Object.hasOwn(inventoryNames, kind) ? inventoryNames[kind] : kind}</td>
            <td data-label="Bron">{item.source ?? 'Onbekend'}</td><td data-label="Doel">{item.target ?? 'Ontbreekt'}</td>
            <td data-label="Inhoud">{item.same ? 'Gelijk' : 'Wijkt af'}</td>
          </tr>)}</tbody>
        </table></div>
        {data.commissioning_gate && <p>{data.commissioning_gate.same
          ? 'Het inbedrijfstellingsbeleid komt overeen.' : 'Het inbedrijfstellingsbeleid wijkt af.'}</p>}
      </details>}
      <h4>Functies</h4>
      <div className="table-wrap">
        <table className="stack-mobile">
          <thead><tr><th scope="col">Functie</th><th scope="col">Apparaat</th><th scope="col">Status</th><th scope="col">Toelichting</th></tr></thead>
          <tbody>
            {data.functions.map((f) => (
              <tr key={JSON.stringify([f.home_id, f.key, f.device_id, f.binding_id])}>
                <td data-label="Functie">{f.label}{data.homes?.[f.home_id] && <div className="small muted">{data.homes[f.home_id]}</div>}</td>
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
        {data.safe_mode && !data.migration && (
          <ActionButton className="btn primary" disabled={release.isPending} onClick={() => { if (window.confirm('Werkt alles zoals verwacht? Na opheffen mag de app weer schakelen.')) release.mutate(undefined); }}>
            Veilige modus opheffen
          </ActionButton>
        )}
      </div>
      {data.migration && <MigrationApproval key={data.migration.confirmation} migration={data.migration} pending={release.isPending} onConfirm={release.mutate} />}
      {!data.safe_mode && <div className="note ok" role="status">Veilige modus is opgeheven; de app schakelt weer.</div>}
      {release.error && <ErrorNote error={release.error} />}
    </div>
  );
}

/* ---------- section ---------- */

type Step =
  | { kind: 'upload' }
  | { kind: 'verified'; v: RestoreVerify; passphrase: string }
  | { kind: 'running'; stored: StoredRestore }
  | { kind: 'dump'; v: VerifiedDatabaseDump; passphrase: string }
  | { kind: 'dump-teruggezet'; database: string };


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
        Controleer een back-up en kies hoe je die wilt terugzetten. Kies bij een overstap een afzonderlijke Custom App.
        De vergelijking toont welke apparaten na herstel beschikbaar zijn.
      </p>
      {step.kind === 'upload' && <UploadStep onVerified={(v, passphrase) => {
        if ('kind' in v) setStep({ kind: 'dump', v, passphrase: v.dump_upload_id ? passphrase : '' });
        else setStep({ kind: 'verified', v, passphrase });
      }} />}
      {step.kind === 'dump' && <DatabaseDumpHerstel dump={step.v} passphrase={step.passphrase}
        onBack={() => setStep({ kind: 'upload' })} onDone={database => setStep({ kind: 'dump-teruggezet', database })} />}
      {step.kind === 'dump-teruggezet' && <div className="note info stack" role="status">
        <strong>Afzonderlijke database hersteld</strong>
        <span>De gegevens staan in <code>{step.database}</code>. Controleer de kopie vóór ingebruikname.</span>
        <div><button className="btn" onClick={() => setStep({ kind: 'upload' })}>Ander bestand kiezen</button></div>
      </div>}
      {step.kind === 'verified' && (
        <ChooseStep v={step.v} passphrase={step.passphrase} onStarted={(stored) => setStep({ kind: 'running', stored })} onCancel={() => setStep({ kind: 'upload' })} />
      )}
      {step.kind === 'running' && <RunningStep stored={step.stored} onReset={() => setStep({ kind: 'upload' })} />}
      {compare.isLoading && (safeMode || step.kind === 'running') && <Loading label="Vergelijking laden…" />}
      {compare.error && <ErrorNote error={compare.error} />}
      {cmp && (cmp.safe_mode || step.kind === 'running') && (
        <CompareView data={cmp} onReleased={() => setStep({ kind: 'upload' })} />
      )}
      {step.kind === 'upload' && <ZigbeeHerstelkopie />}
      <SourcePauseForm />
      <RekeyForm />
    </section>
  );
}
