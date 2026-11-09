import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, isApiError } from '../api/client';
import { qk } from '../api/keys';
import type { Integration, IntegrationDetail } from '../api/types';
import { ActionButton, CopyButton, ErrorNote, Loading, StateLabel, yesNo } from '../components/bits';
import { ago, dateTime } from '../format';
import { HEALTH_FIELD_LABEL, LIFECYCLE_LABEL, PROVIDER_LABEL, t } from '../i18n';
import { CODE_HINT, kindLabel } from './labels';

function isTs(v: unknown): v is string {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v);
}
function fmt(v: unknown): string {
  if (v === null || v === undefined || v === '') return '–';
  if (typeof v === 'boolean') return yesNo(v);
  if (typeof v === 'number') return v.toLocaleString('nl-NL');
  if (isTs(v)) return `${dateTime(v)} (${ago(v)})`;
  if (Array.isArray(v)) return v.length ? v.map(fmt).join(', ') : 'geen';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/** Health facts of an integration (verbeterlijst 26, 28): connected since, last OK, missed polls. */
export function healthLines(i: Integration): string[] {
  const out: string[] = [];
  if (i.connected_since) out.push(`Verbonden sinds ${dateTime(i.connected_since)} (${ago(i.connected_since)})`);
  if (i.last_ok_at) out.push(`Laatst gelukt ${ago(i.last_ok_at)}`);
  if (i.missed_polls) out.push(`${i.missed_polls} uitleesronde${i.missed_polls === 1 ? '' : 's'} gemist`);
  if (i.checked_at) out.push(`Gecontroleerd ${ago(i.checked_at)}`);
  return out;
}

function DetailView({ id }: { id: string }) {
  const q = useQuery({ queryKey: qk.integration(id), queryFn: () => api.integration(id) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  const d: IntegrationDetail | undefined = q.data && typeof q.data === 'object' && !Array.isArray(q.data) ? q.data : undefined;
  if (!d) return null;
  const extra = Object.entries(d.health?.extra ?? {}).filter(([, v]) => v !== null && v !== undefined && (typeof v !== 'object' || Array.isArray(v)));
  const diagText = d.diagnostics && Object.keys(d.diagnostics).length ? JSON.stringify(d.diagnostics, null, 2) : null;
  return (
    <div className="stack tight dv-int-detail" data-testid={`integration-detail-${id}`}>
      <dl className="facts compact">
        {d.health?.health_detail && <div><dt>{HEALTH_FIELD_LABEL.health_detail}</dt><dd>{d.health.health_detail}</dd></div>}
        {d.identity && <div><dt>Identiteit</dt><dd className="break">{d.identity}</dd></div>}
        {extra.map(([k, v]) => <div key={k}><dt>{t(HEALTH_FIELD_LABEL, k)}</dt><dd className="break">{fmt(v)}</dd></div>)}
      </dl>
      {d.devices?.length > 0 && (
        <>
          <h4>Apparaten via deze koppeling</h4>
          <ul className="plain-list small">
            {d.devices.map((x) => (
              <li key={x.id}>{x.name} · {kindLabel(x.kind)} · {x.online ? 'online' : 'offline'}{x.lifecycle !== 'active' ? ` · ${t(LIFECYCLE_LABEL, x.lifecycle).toLowerCase()}` : ''}</li>
            ))}
          </ul>
        </>
      )}
      {diagText && (
        <details>
          <summary>Diagnose van de koppeling</summary>
          <pre className="code-block" tabIndex={0}>{diagText}</pre>
          <div className="row"><CopyButton text={diagText} label="Diagnose kopiëren" /></div>
        </details>
      )}
    </div>
  );
}

/** Change only the address after a DHCP change (verbeterlijst 24). First with an identity check; when the
 * device does not answer, offer to save without checking. */
function AddressForm({ i, onDone }: { i: Integration; onDone: () => void }) {
  const qc = useQueryClient();
  const [host, setHost] = useState(i.host ?? '');
  const save = useMutation({
    mutationFn: (verify: boolean) => api.patchIntegration(i.id!, { host: host.trim(), verify }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.integrations });
      qc.invalidateQueries({ queryKey: qk.integration(i.id!) });
      onDone();
    },
  });
  const unreachable = save.error && isApiError(save.error) && save.error.status >= 500;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (host.trim() && host.trim() !== i.host) save.mutate(true);
  };
  return (
    <form className="stack tight" onSubmit={submit} aria-label={`Adres van ${i.label} wijzigen`}>
      <div className="dv-inline-form">
        <label className="field">
          Nieuw IP-adres
          <input value={host} inputMode="decimal" autoComplete="off" required onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.50" />
        </label>
        <ActionButton type="submit" className="btn primary" disabled={save.isPending || !host.trim() || host.trim() === i.host}>{save.isPending ? 'Controleren…' : 'Controleren en opslaan'}</ActionButton>
        <button type="button" className="btn" onClick={onDone}>Annuleren</button>
      </div>
      <p className="small muted" style={{ margin: 0 }}>De app controleert eerst of op het nieuwe adres hetzelfde apparaat antwoordt. Tip: geef het apparaat een vaste IP-reservering in je router.</p>
      {save.error && <ErrorNote error={save.error} />}
      {unreachable && (
        <div>
          <ActionButton
            className="btn"
            disabled={save.isPending}
            onClick={() => {
              if (window.confirm(`Adres ${host.trim()} opslaan zonder controle? Gebruik dit alleen als het apparaat nu uit staat. Antwoordt daar later een ander apparaat, dan meldt de app dat.`)) save.mutate(false);
            }}
          >
            Toch opslaan zonder controle
          </ActionButton>
        </div>
      )}
    </form>
  );
}

function IntegrationCard({ i, admin }: { i: Integration; admin: boolean }) {
  const [details, setDetails] = useState(false);
  const [editing, setEditing] = useState(false);
  const lines = healthLines(i);
  const hint = i.code ? CODE_HINT[i.code] : undefined;
  return (
    <article className="card tight live dv-int" aria-labelledby={`int-${i.id}`} data-testid={`integration-${i.id}`}>
      <div className="section-head">
        <h3 id={`int-${i.id}`}>{i.label}</h3>
        <span className="small muted">{i.provider_label ?? t(PROVIDER_LABEL, i.provider)}{i.host ? ` · ${i.host}` : ''}</span>
      </div>
      <StateLabel state={i.state}>{i.detail || undefined}</StateLabel>
      {hint && i.state !== 'ok' && <p className="small" style={{ margin: 0 }}>{hint}</p>}
      {lines.length > 0 && <ul className="plain-list small muted">{lines.map((l) => <li key={l}>{l}</li>)}</ul>}
      <div className="row" style={{ gap: 6 }}>
        {admin && (
          <button type="button" className="btn small" aria-expanded={details} aria-label={`${details ? 'Details verbergen' : 'Details'}: ${i.label}`} onClick={() => setDetails(!details)}>
            {details ? 'Details verbergen' : 'Details'}
          </button>
        )}
        {admin && i.provider !== 'zigbee' && !editing && (
          <button type="button" className="btn small" aria-label={`Adres wijzigen: ${i.label}`} onClick={() => setEditing(true)}>Adres wijzigen</button>
        )}
      </div>
      {editing && <AddressForm i={i} onDone={() => setEditing(false)} />}
      {details && i.id && <DetailView id={i.id} />}
    </article>
  );
}

/** Integrations with their health (state, connected since, missed polls), address change and details. */
export function IntegrationsSection({ admin }: { admin: boolean }) {
  const q = useQuery({ queryKey: qk.integrations, queryFn: () => api.integrations(), refetchInterval: 60_000 });
  const list = Array.isArray(q.data) ? q.data : [];
  const configured = list.filter((i) => i.id);
  const missing = list.filter((i) => !i.id);
  return (
    <section aria-labelledby="int-h" className="section">
      <h2 id="int-h">Koppelingen</h2>
      {q.isLoading && <Loading />}
      {q.error && <ErrorNote error={q.error} />}
      {configured.length > 0 && <div className="grid-status wide">{configured.map((i) => <IntegrationCard key={i.id} i={i} admin={admin} />)}</div>}
      {q.isSuccess && configured.length === 0 && <p className="small muted" style={{ margin: 0 }}>Nog niets gekoppeld.</p>}
      {missing.length > 0 && (
        <p className="small muted" style={{ margin: 0 }}>
          Niet ingesteld: {missing.map((i) => i.provider_label ?? i.label).join(', ')}.
        </p>
      )}
    </section>
  );
}
