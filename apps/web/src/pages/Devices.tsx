import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, isActionRequired } from '../api/client';
import { BIND_ROLES, type ActionRequired, type Device, type DiscoveredDevice, type Room } from '../api/types';
import { useHome } from '../state';
import { useIsAdmin } from '../user';
import { ActionButton, Dot, Empty, ErrorNote, Loading } from '../components/bits';
import { ago } from '../format';

const PROVIDERS: { id: string; label: string; hostLabel: string; help: string }[] = [
  { id: 'hue', label: 'Philips Hue', hostLabel: 'IP-adres Hue Bridge', help: 'Zoek de bridge of vul het IP-adres in. Daarna vraagt de app je op de ronde link-knop te drukken.' },
  { id: 'homewizard', label: 'HomeWizard', hostLabel: 'IP-adres P1-meter, Energy Socket of kWh-meter', help: 'Zet in de HomeWizard Energy-app per apparaat "Lokale API" aan. Nieuwere apparaten vragen om een druk op de knop van het apparaat.' },
  { id: 'gree', label: 'Gree airco', hostLabel: 'IP-adres binnenunit', help: 'Koppel elke binnenunit eerst via de Gree+-app aan je 2,4 GHz-wifi. Niet elk model ondersteunt lokale bediening; onbekende modellen krijgen de status "nog niet getest".' },
];

export const PROTECTED_TEXT = 'Alleen meten — schakelen geblokkeerd. Let op: de HomeWizard-app kan de stekker nog wel schakelen.';

const COMPAT_LABEL: Record<Device['compatibility'], string> = { tested: 'getest', untested: 'nog niet getest', unsupported: 'niet ondersteund' };

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </svg>
  );
}

function CompatBadge({ c }: { c: Device['compatibility'] }) {
  const cls = c === 'tested' ? 'measured' : c === 'untested' ? 'missing' : 'missing';
  return <span className={`q ${cls}`}>{COMPAT_LABEL[c]}</span>;
}

function ActionSteps({ action, onRetry, busy }: { action: ActionRequired; onRetry: () => void; busy: boolean }) {
  return (
    <div className="note info" role="status" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <strong>Actie nodig</strong>
      <ol style={{ margin: 0, paddingLeft: 18 }}>{action.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      {action.retry_after_s ? <span>Probeer het daarna opnieuw (binnen ±{action.retry_after_s} s).</span> : null}
      <div><ActionButton className="btn primary" disabled={busy} onClick={onRetry}>{busy ? 'Bezig…' : 'Opnieuw proberen'}</ActionButton></div>
    </div>
  );
}

function ProviderCard({ id, label, hostLabel, help, homeId }: (typeof PROVIDERS)[number] & { homeId: string }) {
  const qc = useQueryClient();
  const [host, setHost] = useState('');
  const [name, setName] = useState('');
  const [found, setFound] = useState<DiscoveredDevice[] | null>(null);
  const [action, setAction] = useState<ActionRequired | null>(null);
  const [lastHost, setLastHost] = useState('');
  const [paired, setPaired] = useState<string | null>(null);
  const discover = useMutation({ mutationFn: () => api.discover(id, homeId), onSuccess: setFound });
  const pair = useMutation({
    mutationFn: (h: string) => api.pair(id, { home_id: homeId, host: h, ...(name.trim() ? { name: name.trim() } : {}) }),
    onMutate: (h) => setLastHost(h),
    onSuccess: (r) => {
      if (isActionRequired(r)) {
        setAction(r);
        setPaired(null);
      } else {
        setAction(null);
        setPaired(r.name);
        qc.invalidateQueries({ queryKey: ['devices'] });
        qc.invalidateQueries({ queryKey: ['integrations'] });
      }
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    pair.mutate(host.trim());
  };
  return (
    <article className="card" aria-labelledby={`p-${id}`}>
      <h3 id={`p-${id}`}>{label}</h3>
      <p className="small muted" style={{ margin: 0 }}>{help}</p>
      <div className="row" style={{ gap: 8 }}>
        <ActionButton className="btn" disabled={discover.isPending} onClick={() => discover.mutate()}>{discover.isPending ? 'Zoeken…' : 'Zoeken op netwerk'}</ActionButton>
      </div>
      {found && (found.length ? (
        <ul className="meter-list">
          {found.map((d) => (
            <li key={d.stable_identity}>
              <span>{d.name}<span className="small muted"> · {d.model ?? d.kind}{d.host ? ` · ${d.host}` : ''}</span></span>
              <ActionButton className="btn" disabled={pair.isPending || !d.host} onClick={() => d.host && pair.mutate(d.host)}>Koppelen</ActionButton>
            </li>
          ))}
        </ul>
      ) : <Empty>Niets gevonden. Vul hieronder het IP-adres in.</Empty>)}
      <form className="form-grid" onSubmit={submit}>
        <label className="field">{hostLabel}<input value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.50" required /></label>
        <label className="field">Naam (optioneel)<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <ActionButton type="submit" className="btn primary" disabled={pair.isPending}>Koppelen</ActionButton>
      </form>
      {action && <ActionSteps action={action} busy={pair.isPending} onRetry={() => pair.mutate(lastHost)} />}
      {paired && <div className="note info" role="status">Gekoppeld: {paired}</div>}
      {(discover.error || pair.error) && <ErrorNote error={discover.error ?? pair.error} />}
    </article>
  );
}

const JOIN_SECONDS = 120;

function NewSensorRow({ d, rooms }: { d: Device; rooms: Room[] }) {
  const qc = useQueryClient();
  const current = rooms.find((r) => r.name === d.room)?.id ?? '';
  const m = useMutation({
    mutationFn: (room_id: string) => api.patchDevice(d.id, { room_id: room_id || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  });
  return (
    <li>
      <span><span className="q measured">Nieuw gevonden</span> <strong>{d.name}</strong><span className="small muted"> · {d.model ?? d.stable_identity}</span></span>
      <label className="field" style={{ gap: 2 }}>
        <span className="sr-only">Ruimte voor {d.name}</span>
        <select value={current} disabled={m.isPending} onChange={(e) => m.mutate(e.target.value)}>
          <option value="">Kies ruimte…</option>
          {rooms.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
      </label>
      {m.error && <ErrorNote error={m.error} />}
    </li>
  );
}

function ZigbeeCard({ homeId }: { homeId: string }) {
  const qc = useQueryClient();
  const instr = useQuery({ queryKey: ['instructions', 'zigbee'], queryFn: () => api.integrationInstructions('zigbee') });
  const integrations = useQuery({ queryKey: ['integrations'], queryFn: api.integrations });
  const rooms = useQuery({ queryKey: ['rooms', homeId], queryFn: () => api.rooms(homeId) });
  const [openUntil, setOpenUntil] = useState<number | null>(null);
  const [known, setKnown] = useState<Set<string> | null>(null);
  const [now, setNow] = useState(Date.now());
  const remaining = openUntil ? Math.max(0, Math.ceil((openUntil - now) / 1000)) : 0;
  const isOpen = remaining > 0;
  const devices = useQuery({
    queryKey: ['devices', homeId],
    queryFn: () => api.devices(homeId),
    refetchInterval: isOpen ? 5000 : 60_000,
  });
  useEffect(() => {
    if (!openUntil) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [openUntil]);
  const configured = integrations.data?.some((i) => i.provider === 'zigbee' && i.id) ?? false;
  const setup = useMutation({
    mutationFn: () => api.zigbeeSetup(homeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integrations'] });
      qc.invalidateQueries({ queryKey: ['devices'] });
    },
  });
  const join = useMutation({
    mutationFn: () => api.zigbeePermitJoin(homeId, JOIN_SECONDS),
    onSuccess: (r) => {
      setKnown(new Set((devices.data ?? []).filter((d) => d.kind === 'zigbee_sensor').map((d) => d.id)));
      setNow(Date.now());
      setOpenUntil(new Date(r.open_until).getTime());
    },
  });
  const fresh = known ? (devices.data ?? []).filter((d) => d.kind === 'zigbee_sensor' && !known.has(d.id)) : [];
  const mmss = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
  return (
    <article className="card" aria-labelledby="p-zigbee">
      <h3 id="p-zigbee">Zigbee · Sensor koppelen</h3>
      {instr.isLoading && <Loading />}
      {instr.error && <ErrorNote error={instr.error} />}
      {instr.data && (
        <>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.5 }}>{instr.data.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
          {instr.data.needs_manufacturer_app && <p className="small muted" style={{ margin: 0 }}>{instr.data.needs_manufacturer_app}</p>}
        </>
      )}
      <div className="row" style={{ gap: 8 }}>
        {!configured ? (
          <ActionButton className="btn primary" disabled={setup.isPending || integrations.isLoading} onClick={() => setup.mutate()}>{setup.isPending ? 'Bezig…' : 'Zigbee instellen'}</ActionButton>
        ) : (
          <ActionButton className="btn primary" disabled={join.isPending || isOpen} onClick={() => join.mutate()}>Koppelen starten (2 min)</ActionButton>
        )}
        {isOpen && <span className="small" role="timer" aria-live="off" data-testid="join-countdown">Pairing open · nog {mmss}</span>}
        {openUntil && !isOpen && <span className="small muted">Pairingvenster gesloten.</span>}
      </div>
      {(setup.error || join.error) && <ErrorNote error={setup.error ?? join.error} />}
      {known && (fresh.length ? (
        <ul className="meter-list" aria-label="Nieuwe sensoren">
          {fresh.map((d) => <NewSensorRow key={d.id} d={d} rooms={rooms.data ?? []} />)}
        </ul>
      ) : isOpen ? <span className="small muted" role="status">Zoeken naar nieuwe sensoren…</span> : null)}
    </article>
  );
}

function DeviceRow({ d, isAdmin }: { d: Device; isAdmin: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(d.name);
  const refresh = () => qc.invalidateQueries({ queryKey: ['devices'] });
  const rename = useMutation({ mutationFn: () => api.patchDevice(d.id, { name: name.trim() }), onSuccess: () => { setEditing(false); refresh(); } });
  const bind = useMutation({ mutationFn: (role: string) => api.bindDevice(d.id, role), onSuccess: refresh });
  const currentRole = BIND_ROLES.find((r) => d.roles.includes(r.key))?.key ?? '';
  return (
    <tr>
      <td>
        {editing ? (
          <form className="row" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) rename.mutate(); }}>
            <label className="sr-only" htmlFor={`n-${d.id}`}>Naam</label>
            <input id={`n-${d.id}`} className="input" style={{ minHeight: 44, padding: '0 10px', borderRadius: 10, border: '1px solid var(--border-strong)', background: 'var(--surface)' }} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            <ActionButton type="submit" className="btn primary" disabled={rename.isPending}>Opslaan</ActionButton>
            <button type="button" className="btn" onClick={() => { setEditing(false); setName(d.name); }}>Annuleren</button>
          </form>
        ) : (
          <span className="row" style={{ gap: 6 }}>
            <strong>{d.name}</strong>
            {isAdmin && <button type="button" className="btn" style={{ minHeight: 32, padding: '0 8px', fontSize: 13 }} aria-label={`${d.name} hernoemen`} onClick={() => setEditing(true)}>Hernoemen</button>}
          </span>
        )}
        <div className="small muted">{d.kind}{d.model ? ` · ${d.model}` : ''} · {d.stable_identity}</div>
        {rename.error && <ErrorNote error={rename.error} />}
      </td>
      <td>{d.provider}</td>
      <td>
        {isAdmin ? (
          <label className="field" style={{ gap: 2 }}>
            <span className="sr-only">Rol van {d.name}</span>
            <select className="input" value={currentRole} disabled={bind.isPending} onChange={(e) => e.target.value && bind.mutate(e.target.value)}>
              <option value="">{d.roles.length && !currentRole ? d.roles.join(', ') : 'Geen meterrol'}</option>
              {BIND_ROLES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select>
          </label>
        ) : (
          d.roles.join(', ') || '–'
        )}
        {d.protected && (
          <div className="small row" style={{ gap: 6, color: 'var(--warn)', marginTop: 6, flexWrap: 'nowrap', alignItems: 'flex-start' }}>
            <span style={{ marginTop: 2 }}><LockIcon /></span><span>{PROTECTED_TEXT}</span>
          </div>
        )}
        {bind.error && <ErrorNote error={bind.error} />}
      </td>
      <td><span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}><Dot state={d.online ? 'ok' : 'error'} />{d.online ? 'online' : `offline · ${ago(d.last_seen)}`}</span></td>
      <td><CompatBadge c={d.compatibility} /></td>
    </tr>
  );
}

export function DevicesPage() {
  const { homeId } = useHome();
  const isAdmin = useIsAdmin();
  const devices = useQuery({ queryKey: ['devices', homeId], queryFn: () => api.devices(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  const integrations = useQuery({ queryKey: ['integrations'], queryFn: api.integrations, refetchInterval: 60_000 });
  return (
    <>
      <header className="page-head"><div><span className="eyebrow">Installatie</span><h1>Apparaten</h1></div></header>
      <section aria-labelledby="int-h" className="section">
        <h2 id="int-h">Integraties</h2>
        {integrations.isLoading && <Loading />}
        {integrations.error && <ErrorNote error={integrations.error} />}
        <div className="grid-status">
          {integrations.data?.map((i) => (
            <div key={i.id ?? i.provider} className="card tight live">
              <span className="card-sub">{i.label}</span>
              <span className={`state-line ${i.state}`}><Dot state={i.state} />{i.state === 'not_configured' ? 'Niet ingesteld' : i.detail}</span>
              {i.state === 'not_configured' && i.detail && <span className="small muted">{i.detail}</span>}
              {i.last_ok_at && <span className="small muted">laatst OK {ago(i.last_ok_at)}</span>}
            </div>
          ))}
        </div>
      </section>
      <section aria-labelledby="dev-h" className="card">
        <h2 id="dev-h">Gekoppelde apparaten</h2>
        {devices.isLoading && <Loading />}
        {devices.data?.length === 0 && <Empty>Nog geen apparaten gekoppeld.</Empty>}
        {devices.data && devices.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Naam</th><th>Merk</th><th>Rol</th><th>Status</th><th>Compatibiliteit</th></tr></thead>
              <tbody>{devices.data.map((d) => <DeviceRow key={d.id} d={d} isAdmin={isAdmin} />)}</tbody>
            </table>
          </div>
        )}
      </section>
      {isAdmin && homeId && (
        <section aria-labelledby="add-h" className="section">
          <h2 id="add-h">Apparaat toevoegen</h2>
          <div className="grid-auto">
            {PROVIDERS.map((p) => <ProviderCard key={p.id} {...p} homeId={homeId} />)}
            <ZigbeeCard homeId={homeId} />
          </div>
        </section>
      )}
    </>
  );
}
