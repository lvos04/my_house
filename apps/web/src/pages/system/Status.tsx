import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import type { IntegrationCheckDetails, StatusCheck } from '../../api/types';
import { ErrorNote, Loading, yesNo } from '../../components/bits';
import { StatusBadge, statusVan } from '../../components/peil/StatusBadge';
import { APP_VERSION, ago, dateTime } from '../../format';
import { HEALTH_FIELD_LABEL, t } from '../../i18n';

const SERVER_ORDER = ['database', 'worker', 'worker_loops', 'maintenance', 'safe_mode', 'restore', 'time', 'disk', 'backup'];

function isTs(v: unknown): v is string {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v);
}
function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '–';
  if (typeof v === 'boolean') return yesNo(v);
  if (typeof v === 'number') return v.toLocaleString('nl-NL');
  if (isTs(v)) return `${dateTime(v)} (${ago(v)})`;
  if (Array.isArray(v)) return v.length ? v.map((x) => fmtValue(x)).join(', ') : 'geen';
  if (typeof v === 'object') return Object.keys(v as object).length ? JSON.stringify(v) : '–';
  return String(v);
}

/** Health details of an integration check, as label/value pairs in Dutch. */
export function detailRows(d: IntegrationCheckDetails): [string, string][] {
  const rows: [string, string][] = [];
  const main: (keyof IntegrationCheckDetails)[] = ['health_detail', 'since', 'connected_since', 'missed_polls', 'consecutive_failures', 'checked_at', 'code'];
  for (const k of main) {
    const v = d[k];
    if (v === null || v === undefined || v === '' || (k !== 'missed_polls' && k !== 'consecutive_failures' && v === 0)) continue;
    rows.push([t(HEALTH_FIELD_LABEL, k), fmtValue(v)]);
  }
  for (const [k, v] of Object.entries(d.extra ?? {})) {
    if (v === null || v === undefined || typeof v === 'object' && !Array.isArray(v)) continue;
    rows.push([t(HEALTH_FIELD_LABEL, k), fmtValue(v)]);
  }
  return rows;
}

function extraRows(c: StatusCheck): [string, string][] {
  const rows: [string, string][] = [];
  if (c.key === 'worker' && typeof c.age_s === 'number') rows.push(['Laatste hartslag', `${c.age_s} s geleden`]);
  if (c.key === 'backup') {
    if (c.app_version || c.schema_revision) rows.push(['Versie laatste back-up', `app ${c.app_version ?? '?'} · schema ${c.schema_revision ?? '?'}`]);
    if (c.zigbee2mqtt_included !== undefined) rows.push(['Zigbee in laatste back-up', yesNo(c.zigbee2mqtt_included)]);
    if (c.auto_failures) rows.push(['Automatische back-up mislukt', `${c.auto_failures}× · ${c.auto_last_error ?? ''}`]);
  }
  if (c.details) rows.push(...detailRows(c.details));
  return rows;
}

export function CheckCard({ check }: { check: StatusCheck }) {
  const rows = extraRows(check);
  return (
    <div className="card tight live check-card" data-testid={`check-${check.key}`}>
      <span className="card-sub">{check.label}</span>
      {/* SysteemStatusPaneel-rij (Peil C-04): StatusBadge met vast woord + reden in inkt. */}
      <span className={`state-line ${check.state}`}>
        <StatusBadge status={statusVan(check.state)} />
        {check.detail ? <span className="font-normal">{check.detail}</span> : null}
      </span>
      {rows.length > 0 && (
        <details className="check-details">
          <summary>Details</summary>
          <dl className="facts compact">
            {rows.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}

export function splitChecks(checks: StatusCheck[]): { server: StatusCheck[]; integrations: StatusCheck[] } {
  const rank = (key: string) => {
    const i = SERVER_ORDER.indexOf(key);
    return i < 0 ? SERVER_ORDER.length : i;
  };
  const server = checks.filter((c) => !c.key.startsWith('integration:')).sort((a, b) => rank(a.key) - rank(b.key));
  return { server, integrations: checks.filter((c) => c.key.startsWith('integration:')) };
}

/** Schema revision from the "database" check ("Schema 0003_followups"). */
export function schemaFrom(checks: StatusCheck[] | undefined): string | null {
  const d = checks?.find((c) => c.key === 'database')?.detail ?? '';
  return d.startsWith('Schema ') ? d.slice(7) : null;
}

export function StatusSection() {
  const status = useQuery({ queryKey: qk.system, queryFn: api.systemStatus, refetchInterval: 60_000 });
  const checks = status.data?.checks ?? [];
  const { server, integrations } = splitChecks(checks);
  const problems = checks.filter((c) => c.state === 'error').length;
  return (
    <section aria-labelledby="st-h" className="section">
      <div className="section-head">
        <h2 id="st-h">Status</h2>
        <button type="button" className="btn small" onClick={() => status.refetch()} disabled={status.isFetching}>
          {status.isFetching ? 'Controleren…' : 'Opnieuw controleren'}
        </button>
      </div>
      {status.isLoading && <Loading />}
      {status.error && <ErrorNote error={status.error} />}
      {status.data && (
        <p className="small muted" role="status" style={{ margin: 0 }}>
          {problems === 0 ? 'Geen problemen gevonden.' : `${problems} ${problems === 1 ? 'probleem' : 'problemen'} gevonden.`}
        </p>
      )}
      {server.length > 0 && (
        <>
          <h3>Server</h3>
          <div className="grid-status wide">{server.map((c) => <CheckCard key={c.key} check={c} />)}</div>
        </>
      )}
      {integrations.length > 0 && (
        <>
          <h3>Koppelingen</h3>
          <div className="grid-status wide">{integrations.map((c) => <CheckCard key={c.key} check={c} />)}</div>
        </>
      )}
    </section>
  );
}

/** App and schema versions (verbeterlijst 34): web app, server, database. */
export function VersionCard() {
  const status = useQuery({ queryKey: qk.system, queryFn: api.systemStatus, refetchInterval: 60_000 });
  const server = status.data?.version ?? null;
  const schema = schemaFrom(status.data?.checks);
  return (
    <section aria-labelledby="ver-h" className="card">
      <h2 id="ver-h">Versie</h2>
      <dl className="facts">
        <div><dt>Web-app</dt><dd>{APP_VERSION}</dd></div>
        <div><dt>Server</dt><dd>{server ?? '–'}</dd></div>
        <div><dt>Databaseschema</dt><dd>{schema ?? '–'}</dd></div>
      </dl>
      {server && server !== APP_VERSION && !APP_VERSION.endsWith('-dev') && (
        <div className="note warn" role="note">
          De web-app ({APP_VERSION}) en de server ({server}) hebben een andere versie. Vernieuw de pagina; helpt dat niet, controleer dan de update.
        </div>
      )}
    </section>
  );
}
