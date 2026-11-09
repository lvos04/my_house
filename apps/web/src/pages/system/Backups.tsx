import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import type { Backup, BackupSchedule, BackupSettings, BackupSettingsIn, RetentionField, RetentionIn, RetentionSettings } from '../../api/types';
import { ActionButton, Empty, ErrorNote, Loading, yesNo } from '../../components/bits';
import { ago, bytes, dateTime, until } from '../../format';
import { BACKUP_KIND_LABEL, BACKUP_SCHEDULE_LABEL, BACKUP_STATUS_LABEL, t } from '../../i18n';

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hh = (h: number) => `${String(h).padStart(2, '0')}:00`;

function isSettings(x: unknown): x is BackupSettings {
  return typeof x === 'object' && x !== null && !Array.isArray(x) && 'hour' in x;
}

const SCHEDULES: BackupSchedule[] = ['daily_full', 'dump_daily_full_weekly'];
/** A row of the backup list is an encrypted database dump (.shcdump), not a full export. */
const isDump = (b: Pick<Backup, 'kind'>) => b.kind === 'db_dump';

/** Automatic backups: on/off, passphrase (stored encrypted), hour, what is made (every day a full export,
 * or every day a database dump and weekly the full export) and how many to keep (verbeterlijst 32). */
export function BackupSettingsForm() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.backupSettings, queryFn: api.backupSettings });
  const s = isSettings(q.data) ? q.data : null;
  const [draft, setDraft] = useState<{ enabled?: boolean; hour?: number; keep?: string; schedule?: BackupSchedule }>({});
  const [pass, setPass] = useState('');
  const [pass2, setPass2] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (b: BackupSettingsIn) => api.putBackupSettings(b),
    onSuccess: (v) => {
      qc.setQueryData(qk.backupSettings, v);
      qc.invalidateQueries({ queryKey: qk.system });
      setDraft({});
      setPass('');
      setPass2('');
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!s) return null;

  const enabled = draft.enabled ?? s.enabled;
  const hour = draft.hour ?? s.hour;
  const keep = draft.keep ?? String(s.keep);
  const savedSchedule: BackupSchedule = s.schedule ?? 'daily_full';
  const schedule = draft.schedule ?? savedSchedule;
  const dumps = savedSchedule === 'dump_daily_full_weekly';
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    const body: BackupSettingsIn = {};
    if (pass) {
      if (pass.length < 12) return setLocalError('De wachtwoordzin moet minimaal 12 tekens zijn.');
      if (pass !== pass2) return setLocalError('De wachtwoordzinnen komen niet overeen.');
      body.passphrase = pass;
    }
    if (enabled && !s.passphrase_set && !pass) return setLocalError('Stel eerst een wachtwoordzin in; zonder kan er geen automatische back-up gemaakt worden.');
    const k = Number(keep);
    if (!Number.isInteger(k) || k < 1 || k > 365) return setLocalError('Bewaar tussen 1 en 365 automatische back-ups.');
    if (enabled !== s.enabled) body.enabled = enabled;
    if (hour !== s.hour) body.hour = hour;
    if (k !== s.keep) body.keep = k;
    if (schedule !== savedSchedule) body.schedule = schedule;
    save.mutate(body);
  };
  const dirty = Boolean(pass) || draft.enabled !== undefined || draft.hour !== undefined || draft.keep !== undefined || draft.schedule !== undefined;
  const nextKind = s.next_kind === 'dump' ? 'database-dump' : 'volledige export';

  return (
    <div className="stack" aria-labelledby="bkset-h">
      <h3 id="bkset-h">Automatische back-ups</h3>
      <dl className="facts">
        <div><dt>Status</dt><dd>{s.enabled ? `Aan, dagelijks rond ${hh(s.hour)} (${s.timezone})` : 'Uit'}</dd></div>
        <div><dt>Wat</dt><dd>{t(BACKUP_SCHEDULE_LABEL, savedSchedule)}</dd></div>
        <div><dt>Volgende</dt><dd>{s.next_due_at ? `${dateTime(s.next_due_at)} (${until(s.next_due_at)})${dumps ? ` · ${nextKind}` : ''}` : '–'}</dd></div>
        <div><dt>{dumps ? 'Laatste automatische export' : 'Laatste automatische'}</dt><dd>{s.last_auto_backup ? `${dateTime(s.last_auto_backup)} (${ago(s.last_auto_backup)})` : 'nog geen'}</dd></div>
        {(dumps || s.last_auto_dump) && (
          <div><dt>Laatste automatische dump</dt><dd>{s.last_auto_dump ? `${dateTime(s.last_auto_dump)} (${ago(s.last_auto_dump)})` : 'nog geen'}</dd></div>
        )}
        <div><dt>Wachtwoordzin</dt><dd>{s.passphrase_set ? (s.passphrase_source === 'file' ? 'ingesteld (bestand op de NAS)' : 'ingesteld in de app') : 'niet ingesteld'}</dd></div>
        <div><dt>{dumps ? 'Zigbee in laatste export' : 'Zigbee in laatste back-up'}</dt><dd>{yesNo(s.zigbee2mqtt_in_last_backup)}</dd></div>
        <div><dt>Bewaard</dt><dd>{dumps ? `${s.keep} automatische exports en ${s.keep} dumps` : `${s.keep} automatische`}</dd></div>
      </dl>
      {s.passphrase_error && <div className="note error" role="alert">{s.passphrase_error}</div>}
      {s.failures > 0 && (
        <div className="note warn" role="note">
          De automatische back-up is {s.failures}× mislukt{s.last_error ? `: ${s.last_error}` : ''}.{s.retry_after ? ` Volgende poging ${until(s.retry_after)}.` : ''}
        </div>
      )}
      <form className="form-grid" onSubmit={submit} aria-label="Instellingen automatische back-ups">
        <label className="check-row">
          <input type="checkbox" checked={enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
          Automatische back-up aan
        </label>
        <label className="field">
          Tijdstip ({s.timezone})
          <select value={hour} onChange={(e) => setDraft({ ...draft, hour: Number(e.target.value) })}>
            {HOURS.map((h) => <option key={h} value={h}>{hh(h)}</option>)}
          </select>
        </label>
        <label className="field">
          Wat maken
          {/* width 100%: a long option must not make the form wider than a phone screen */}
          <select style={{ width: '100%' }} value={schedule} onChange={(e) => setDraft({ ...draft, schedule: e.target.value as BackupSchedule })} aria-describedby="bk-schedule-help">
            {SCHEDULES.map((v) => <option key={v} value={v}>{t(BACKUP_SCHEDULE_LABEL, v)}</option>)}
          </select>
        </label>
        <label className="field">
          {schedule === 'dump_daily_full_weekly' ? 'Aantal bewaren (per soort)' : 'Aantal bewaren'}
          <input type="number" inputMode="numeric" min={1} max={365} value={keep} onChange={(e) => setDraft({ ...draft, keep: e.target.value })} />
        </label>
        <label className="field">
          {s.passphrase_set ? 'Nieuwe wachtwoordzin (optioneel)' : 'Wachtwoordzin (min. 12 tekens)'}
          <input type="password" autoComplete="new-password" value={pass} onChange={(e) => setPass(e.target.value)} />
        </label>
        {pass && (
          <label className="field">
            Herhaal wachtwoordzin
            <input type="password" autoComplete="new-password" value={pass2} onChange={(e) => setPass2(e.target.value)} />
          </label>
        )}
        <ActionButton type="submit" className="btn primary" disabled={save.isPending || !dirty}>{save.isPending ? 'Bezig…' : 'Opslaan'}</ActionButton>
      </form>
      <p id="bk-schedule-help" className="small muted" style={{ margin: 0 }}>
        Een <strong>volledige export</strong> (.shcbak) bevat alles, ook de Zigbee2MQTT-gegevens, en zet je terug bij <a href="#herstel">Herstellen en verhuizen</a>.
        Een <strong>database-dump</strong> (.shcdump) is sneller en kleiner, maar bevat alleen de database; die zet je terug op de NAS via de
        opdrachtregel (<code>shc-api decrypt-dump</code> en daarna <code>pg_restore</code>), niet in de app. Met dagelijks een dump komt er minstens
        elke 7 dagen ook een volledige export. Exports en dumps worden apart opgeruimd.
      </p>
      <p className="small muted" style={{ margin: 0 }}>
        De wachtwoordzin wordt versleuteld op de server bewaard en nooit getoond. Bewaar hem ook zelf (bijvoorbeeld in een wachtwoordmanager):
        zonder wachtwoordzin kan niemand een back-up terugzetten. Alleen automatische back-ups worden opgeruimd; handmatige blijven staan.
      </p>
      {s.passphrase_set && s.passphrase_source === 'app' && (
        <div>
          <ActionButton className="btn danger small" disabled={save.isPending} onClick={() => { if (window.confirm('Wachtwoordzin verwijderen? Automatische back-ups stoppen dan.')) save.mutate({ clear_passphrase: true }); }}>
            Wachtwoordzin verwijderen
          </ActionButton>
        </div>
      )}
      {(localError || save.error) && (localError ? <div className="note error" role="alert">{localError}</div> : <ErrorNote error={save.error} />)}
      {save.isSuccess && !dirty && <span className="small muted" role="status">Opgeslagen.</span>}
    </div>
  );
}

function ManualBackup() {
  const qc = useQueryClient();
  const [passphrase, setPassphrase] = useState('');
  const [includeSecrets, setIncludeSecrets] = useState(true);
  const create = useMutation({
    mutationFn: () => api.createBackup({ passphrase, include_secrets: includeSecrets }),
    onSuccess: () => {
      setPassphrase('');
      qc.invalidateQueries({ queryKey: qk.backups });
      qc.invalidateQueries({ queryKey: qk.system });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (passphrase.length >= 12) create.mutate();
  };
  const b = create.data;
  return (
    <div className="stack">
      <h3>Nu een back-up maken</h3>
      <p className="small muted" style={{ margin: 0 }}>Een back-up is versleuteld met je wachtwoordzin. Zonder die zin kan niemand (ook jij niet) hem terugzetten.</p>
      <form className="form-grid" onSubmit={submit} aria-label="Back-up maken">
        <label className="field">Wachtwoordzin (min. 12 tekens)
          <input type="password" autoComplete="new-password" minLength={12} required value={passphrase} onChange={(e) => setPassphrase(e.target.value)} />
        </label>
        <label className="check-row">
          <input type="checkbox" checked={includeSecrets} onChange={(e) => setIncludeSecrets(e.target.checked)} />
          Geheimen meenemen
        </label>
        <ActionButton type="submit" className="btn primary" disabled={create.isPending || passphrase.length < 12}>{create.isPending ? 'Bezig…' : 'Nu back-up maken'}</ActionButton>
      </form>
      {!includeSecrets && <div className="note warn">Zonder geheimen ontbreken wachtwoorden en apparaatsleutels: na herstel moet je wachtwoorden opnieuw instellen en de Hue Bridge en HomeWizard-apparaten opnieuw koppelen.</div>}
      {create.error && <ErrorNote error={create.error} />}
      {b && (
        <div className="note info" role="status">
          {b.status === 'running'
            ? 'De back-up loopt nog op de achtergrond; de lijst hieronder werkt zichzelf bij.'
            : <>Back-up gemaakt: {b.filename} · {bytes(b.size_bytes)}{b.detail?.tables && <> · {Object.values(b.detail.tables).reduce((x, y) => x + y, 0).toLocaleString('nl-NL')} records</>}</>}
        </div>
      )}
    </div>
  );
}

export function BackupList() {
  const backups = useQuery({
    queryKey: qk.backups,
    queryFn: api.backups,
    refetchInterval: (q) => (Array.isArray(q.state.data) && q.state.data.some((b) => b.status === 'running') ? 5000 : false),
  });
  const list: Backup[] = Array.isArray(backups.data) ? backups.data : [];
  return (
    <div className="stack">
      <h3>Gemaakte back-ups</h3>
      {backups.isLoading && <Loading />}
      {backups.error && <ErrorNote error={backups.error} />}
      {backups.data && list.length === 0 && <Empty>Nog geen back-ups.</Empty>}
      {list.length > 0 && (
        <div className="table-wrap">
          <table className="stack-mobile">
            <caption className="sr-only">Back-ups, nieuwste eerst</caption>
            <thead><tr><th scope="col">Moment</th><th scope="col">Soort</th><th scope="col">Status</th><th scope="col">Versie</th><th scope="col">Inhoud</th><th scope="col">Grootte</th><th scope="col"><span className="sr-only">Downloaden</span></th></tr></thead>
            <tbody>
              {list.map((b) => (
                <tr key={b.id}>
                  <td data-label="Moment">{dateTime(b.created_at)}<div className="small muted break">{b.filename}</div></td>
                  <td data-label="Soort">
                    <span className={`pill ${b.auto ? 'neutral' : 'cool'}`}>{b.auto ? 'Automatisch' : 'Handmatig'}</span>
                    <div className={`small${isDump(b) ? '' : ' muted'}`} style={isDump(b) ? { fontWeight: 600 } : undefined}>{t(BACKUP_KIND_LABEL, b.kind, b.kind)}</div>
                  </td>
                  <td data-label="Status">
                    <span className={`state-line ${b.status === 'ok' ? 'ok' : b.status === 'failed' ? 'error' : 'warn'}`}>{t(BACKUP_STATUS_LABEL, b.status)}</span>
                    {b.error && <div className="small muted">{b.error}</div>}
                  </td>
                  <td data-label="Versie" className="small">app {b.app_version ?? '?'}<br />schema {b.schema_revision ?? '?'}</td>
                  <td data-label="Inhoud" className="small">
                    {isDump(b) ? (
                      <>alleen de database<br /><span className="muted">terugzetten via de opdrachtregel</span></>
                    ) : (
                      <>geheimen {yesNo(b.secrets_included)}<br />Zigbee {yesNo(b.zigbee2mqtt_included)}</>
                    )}
                  </td>
                  <td data-label="Grootte">{bytes(b.size_bytes)}</td>
                  <td>
                    {b.downloadable ? (
                      <a className="btn small" href={api.backupDownloadUrl(b.id)} download aria-label={`${isDump(b) ? 'Database-dump' : 'Back-up'} van ${dateTime(b.created_at)} downloaden`}>Downloaden</a>
                    ) : (
                      <span className="small muted">niet beschikbaar</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const RETENTION_ROWS: { key: RetentionField; label: string; help: string }[] = [
  { key: 'power_days', label: 'Vermogen (detail)', help: 'Momentane wattages; uurtotalen blijven altijd bewaard.' },
  { key: 'counter_days', label: 'Meterstanden (ruw)', help: 'Losse meterstanden; nog niet verwerkte standen worden nooit verwijderd.' },
  { key: 'sensor_days', label: 'Sensormetingen (temperatuur, vochtigheid, batterij)', help: 'Ruwe metingen van sensoren en airco-units; de zelflerende regeling gebruikt de laatste 14 dagen.' },
  { key: 'log_days', label: 'Logboek klimaatregeling', help: 'Regels van het aanpassingslog per zone.' },
];

function isRetention(x: unknown): x is RetentionSettings {
  return typeof x === 'object' && x !== null && !Array.isArray(x) && 'power_days' in x;
}

/** How long raw data is kept (verbeterlijst 19/32). */
export function RetentionForm() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.retention, queryFn: api.retention });
  const r = isRetention(q.data) ? q.data : null;
  const [draft, setDraft] = useState<Partial<Record<RetentionField, string>>>({});
  const save = useMutation({
    mutationFn: (b: RetentionIn) => api.putRetention(b),
    onSuccess: (v) => {
      qc.setQueryData(qk.retention, v);
      setDraft({});
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!r) return null;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body: RetentionIn = {};
    for (const row of RETENTION_ROWS) {
      const v = draft[row.key];
      if (v === undefined) continue;
      body[row.key] = v.trim() === '' ? null : Number(v);
    }
    save.mutate(body);
  };
  return (
    <div className="stack">
      <h3 id="ret-h">Bewaartermijnen</h3>
      <form className="form-grid" onSubmit={submit} aria-labelledby="ret-h">
        {RETENTION_ROWS.map((row) => (
          <div key={row.key} className="field">
            <label htmlFor={`ret-${row.key}`}>{row.label} (dagen)</label>
            <input
              id={`ret-${row.key}`}
              type="number"
              inputMode="numeric"
              min={r.minimums[row.key]}
              max={r.maximum}
              value={draft[row.key] ?? String(r[row.key])}
              onChange={(e) => setDraft({ ...draft, [row.key]: e.target.value })}
              aria-describedby={`ret-${row.key}-help`}
            />
            <span id={`ret-${row.key}-help`} className="small muted">
              {row.help} Minimaal {r.minimums[row.key]}; standaard {r.defaults[row.key]}{r.source[row.key] === 'env' ? ' (nu standaard)' : ''}. Leeg = standaard.
            </span>
          </div>
        ))}
        <ActionButton type="submit" className="btn primary" disabled={save.isPending || Object.keys(draft).length === 0}>Opslaan</ActionButton>
      </form>
      <p className="small muted" style={{ margin: 0 }}>{r.note}</p>
      {save.error && <ErrorNote error={save.error} />}
      {save.isSuccess && Object.keys(draft).length === 0 && <span className="small muted" role="status">Opgeslagen.</span>}
    </div>
  );
}

export function BackupsSection() {
  return (
    <section aria-labelledby="bk-h" className="card" id="backups">
      <h2 id="bk-h">Back-ups en bewaren</h2>
      <BackupSettingsForm />
      <hr />
      <ManualBackup />
      <hr />
      <BackupList />
      <hr />
      <RetentionForm />
    </section>
  );
}
