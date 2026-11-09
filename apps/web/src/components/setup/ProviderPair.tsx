import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, isActionRequired } from '../../api/client';
import { qk } from '../../api/keys';
import type { ActionRequired, DiscoveredDevice, PairResult } from '../../api/types';
import { ActionButton, CopyButton, ErrorNote, Loading, StateLabel } from '../bits';
import { ago } from '../../format';
import { KIND_LABEL, PROVIDER_LABEL, t } from '../../i18n';

export const HOST_LABEL: Record<string, string> = {
  hue: 'IP-adres van de Hue Bridge',
  homewizard: 'IP-adres van de P1-meter, Energy Socket of kWh-meter',
  gree: 'IP-adres van de airco-unit',
};
/** Things to confirm before pairing, so the manufacturer steps are done first. */
const PRECONDITION: Record<string, string> = {
  gree: 'De unit is met de Gree+-app aan mijn 2,4 GHz-wifi gekoppeld en heeft een vast IP-adres in de router.',
  homewizard: 'In de HomeWizard Energy-app staat bij dit apparaat ‘Lokale API’ aan.',
  hue: 'De Hue Bridge is aangesloten en de lampen staan in de Hue-app.',
};

/** Manufacturer instructions for a provider (GET /integrations/{provider}/instructions). */
export function ProviderInstructions({ provider, compact = false }: { provider: string; compact?: boolean }) {
  const q = useQuery({ queryKey: qk.instructions(provider), queryFn: () => api.integrationInstructions(provider), staleTime: 60 * 60_000 });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  const ins = q.data && !Array.isArray(q.data) ? q.data : null;
  if (!ins) return null;
  return (
    <div className="stack tight">
      {ins.needs_manufacturer_app && (
        <div className="note warn" role="note"><strong>Eerst in de app van de fabrikant:</strong> {ins.needs_manufacturer_app}</div>
      )}
      {!compact && ins.works_locally && <p className="small" style={{ margin: 0 }}><strong>Wat lokaal werkt:</strong> {ins.works_locally}</p>}
      {ins.steps?.length > 0 && (
        <ol className="steps-list">
          {ins.steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      )}
    </div>
  );
}

/** Diagnostics of a failed pairing, ready to copy (verbeterlijst 29). */
export function PairDiagnostics({ error, provider, host }: { error: unknown; provider: string; host: string }) {
  if (!(error instanceof ApiError) || !error.detail.diagnostics || !Object.keys(error.detail.diagnostics).length) return null;
  const text = JSON.stringify({ provider, host, code: error.code, message: error.message, diagnostics: error.detail.diagnostics }, null, 2);
  return (
    <details className="diag" open>
      <summary>Diagnose (zonder geheimen)</summary>
      <p className="small muted" style={{ margin: '4px 0' }}>Stuur dit mee als je hulp vraagt of een probleem meldt.</p>
      <pre className="code-block" tabIndex={0}>{text}</pre>
      <div className="row"><CopyButton text={text} label="Diagnose kopiëren" /></div>
    </details>
  );
}

/** Pair one or more devices of a provider: instructions first, then discovery or a manual address,
 * "press the link button" retries, and diagnostics when it fails. */
export function ProviderPair({ provider, homeId }: { provider: string; homeId: string }) {
  const qc = useQueryClient();
  const [host, setHost] = useState('');
  const [name, setName] = useState('');
  const [encryption, setEncryption] = useState('auto');
  const [ready, setReady] = useState(!PRECONDITION[provider]);
  const [action, setAction] = useState<ActionRequired | null>(null);
  const [wait, setWait] = useState(0);
  const [paired, setPaired] = useState<PairResult[]>([]);
  const integrations = useQuery({ queryKey: qk.integrations, queryFn: () => api.integrations() });
  const mine = (Array.isArray(integrations.data) ? integrations.data : []).filter((i) => i.provider === provider && i.id && (!i.home_id || i.home_id === homeId));

  const discover = useMutation({ mutationFn: () => api.discover(provider, homeId) });
  const pair = useMutation({
    mutationFn: () =>
      api.pair(provider, {
        home_id: homeId,
        host: host.trim(),
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(provider === 'gree' && encryption !== 'auto' ? { options: { encryption } } : {}),
      }),
    onSuccess: (r) => {
      if (isActionRequired(r)) {
        setAction(r);
        setWait(r.retry_after_s ?? 0);
        return;
      }
      setAction(null);
      setPaired((p) => [r, ...p]);
      setHost('');
      setName('');
      qc.invalidateQueries({ queryKey: qk.integrations });
      qc.invalidateQueries({ queryKey: qk.devices(homeId) });
      qc.invalidateQueries({ queryKey: qk.system });
    },
  });
  useEffect(() => {
    if (wait <= 0) return;
    const tm = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(tm);
  }, [wait]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (host.trim()) pair.mutate();
  };
  const label = t(PROVIDER_LABEL, provider);
  const found: DiscoveredDevice[] = Array.isArray(discover.data) ? discover.data : [];

  return (
    <div className="stack">
      <ProviderInstructions provider={provider} />
      {mine.length > 0 && (
        <div className="stack tight">
          <h4>Al gekoppeld</h4>
          <ul className="meter-list">
            {mine.map((i) => (
              <li key={i.id}>
                <span>{i.label}{i.host ? <span className="small muted"> · {i.host}</span> : null}</span>
                <span className="small">
                  <StateLabel state={i.state}>{i.detail || undefined}</StateLabel>
                  {i.last_ok_at && <span className="muted"> · laatst OK {ago(i.last_ok_at)}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {PRECONDITION[provider] && (
        <label className="check-row">
          <input type="checkbox" checked={ready} onChange={(e) => setReady(e.target.checked)} />
          {PRECONDITION[provider]}
        </label>
      )}
      {ready && (
        <>
          <div className="row">
            <ActionButton className="btn" disabled={discover.isPending} onClick={() => discover.mutate()}>{discover.isPending ? 'Zoeken…' : `${label} zoeken in het netwerk`}</ActionButton>
            <span className="small muted">Lukt zoeken niet (in de container is dat normaal), vul dan het IP-adres in.</span>
          </div>
          {discover.error && <ErrorNote error={discover.error} />}
          {discover.isSuccess && found.length === 0 && <p className="small muted" style={{ margin: 0 }} role="status">Niets gevonden. Vul het IP-adres handmatig in.</p>}
          {found.length > 0 && (
            <ul className="meter-list" aria-label="Gevonden apparaten">
              {found.map((d) => (
                <li key={d.stable_identity}>
                  <span>{d.name}<span className="small muted"> · {t(KIND_LABEL, d.kind)}{d.model ? ` · ${d.model}` : ''}{d.host ? ` · ${d.host}` : ''}</span></span>
                  {d.host && <button type="button" className="btn small" onClick={() => setHost(d.host!)}>Dit adres gebruiken</button>}
                </li>
              ))}
            </ul>
          )}
          <form className="form-grid" onSubmit={submit} aria-label={`${label} koppelen`}>
            <label className="field">
              {HOST_LABEL[provider] ?? 'IP-adres'}
              <input inputMode="decimal" required placeholder="192.168.1.50" value={host} onChange={(e) => setHost(e.target.value)} autoComplete="off" />
            </label>
            <label className="field">
              Naam (optioneel)
              <input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            </label>
            {provider === 'gree' && (
              <label className="field">
                Versleuteling
                <select value={encryption} onChange={(e) => setEncryption(e.target.value)}>
                  <option value="auto">Automatisch (aanbevolen)</option>
                  <option value="v1">Oud (v1)</option>
                  <option value="v2">Nieuw (v2)</option>
                </select>
              </label>
            )}
            <ActionButton type="submit" className="btn primary" disabled={pair.isPending || !host.trim()}>{pair.isPending ? 'Koppelen…' : 'Koppelen'}</ActionButton>
          </form>
        </>
      )}
      {action && (
        <div className="note info" role="status">
          <strong>Nog één stap nodig:</strong>
          <ol className="steps-list">{action.steps.map((s) => <li key={s}>{s}</li>)}</ol>
          <ActionButton className="btn primary" disabled={pair.isPending || wait > 0} onClick={() => pair.mutate()}>
            {wait > 0 ? `Opnieuw proberen (${wait} s)` : 'Opnieuw proberen'}
          </ActionButton>
        </div>
      )}
      {pair.error && (
        <>
          <ErrorNote error={pair.error} />
          <PairDiagnostics error={pair.error} provider={provider} host={host} />
        </>
      )}
      {paired.map((d) => (
        <div key={d.id} className="note ok" role="status">
          Gekoppeld: {d.name} ({t(KIND_LABEL, d.kind)}){d.repaired ? ' — bestaande koppeling bijgewerkt, geschiedenis blijft' : ''}.
          {d.compatibility === 'unsupported' && ' Dit model wordt (nog) niet ondersteund; de diagnose is bewaard bij het apparaat.'}
        </div>
      ))}
    </div>
  );
}
