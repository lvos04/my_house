import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, isActionRequired } from '../api/client';
import { qk } from '../api/keys';
import type { ActionRequired, DiscoveredDevice, PairResult, Room } from '../api/types';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';
import { PairDiagnostics } from '../components/setup/ProviderPair';
import { KIND_LABEL, t } from '../i18n';

export const PROVIDERS: { id: string; label: string; hostLabel: string; help: string }[] = [
  { id: 'hue', label: 'Philips Hue', hostLabel: 'IP-adres Hue Bridge', help: 'Zoek de bridge of vul het IP-adres in. Daarna vraagt de app je op de ronde link-knop te drukken.' },
  { id: 'homewizard', label: 'HomeWizard', hostLabel: 'IP-adres P1-meter, Energy Socket of kWh-meter', help: 'Zet in de HomeWizard Energy-app per apparaat ‘Lokale API’ aan. Nieuwere apparaten vragen om een druk op de knop van het apparaat.' },
  { id: 'gree', label: 'Gree airco', hostLabel: 'IP-adres binnenunit', help: 'Koppel elke binnenunit eerst via de Gree+-app aan je 2,4 GHz-wifi. Niet elk model ondersteunt lokale bediening; onbekende modellen krijgen de status ‘nog niet getest’.' },
];

function ActionSteps({ action, onRetry, busy }: { action: ActionRequired; onRetry: () => void; busy: boolean }) {
  return (
    <div className="note info stack tight" role="status">
      <strong>Actie nodig</strong>
      <ol className="steps-list">{action.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      {action.retry_after_s ? <span>Probeer het daarna opnieuw (binnen ±{action.retry_after_s} s).</span> : null}
      <div><ActionButton className="btn primary" disabled={busy} onClick={onRetry}>{busy ? 'Bezig…' : 'Opnieuw proberen'}</ActionButton></div>
    </div>
  );
}

/** Pair a Hue Bridge, HomeWizard meter or Gree unit. Pairing the same device again updates the existing
 * link (history stays); a failure shows the diagnostics to copy (verbeterlijst 24, 29). */
export function ProviderCard({ id, label, hostLabel, help, homeId }: (typeof PROVIDERS)[number] & { homeId: string }) {
  const qc = useQueryClient();
  const [host, setHost] = useState('');
  const [name, setName] = useState('');
  const [found, setFound] = useState<DiscoveredDevice[] | null>(null);
  const [action, setAction] = useState<ActionRequired | null>(null);
  const [lastHost, setLastHost] = useState('');
  const [paired, setPaired] = useState<PairResult | null>(null);
  const discover = useMutation({ mutationFn: () => api.discover(id, homeId), onSuccess: (r) => setFound(Array.isArray(r) ? r : []) });
  const pair = useMutation({
    mutationFn: (h: string) => api.pair(id, { home_id: homeId, host: h, ...(name.trim() ? { name: name.trim() } : {}) }),
    onMutate: (h) => setLastHost(h),
    onSuccess: (r) => {
      if (isActionRequired(r)) {
        setAction(r);
        setPaired(null);
      } else {
        setAction(null);
        setPaired(r);
        qc.invalidateQueries({ queryKey: ['devices'] });
        qc.invalidateQueries({ queryKey: qk.integrations });
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
        <ul className="meter-list" aria-label={`Gevonden ${label}-apparaten`}>
          {found.map((d) => (
            <li key={d.stable_identity}>
              <span>{d.name}<span className="small muted"> · {d.model ?? t(KIND_LABEL, d.kind)}{d.host ? ` · ${d.host}` : ''}</span></span>
              <ActionButton className="btn" disabled={pair.isPending || !d.host} onClick={() => d.host && pair.mutate(d.host)} aria-label={`${d.name} koppelen`}>Koppelen</ActionButton>
            </li>
          ))}
        </ul>
      ) : <Empty>Niets gevonden (in het containernetwerk is dat normaal). Vul hieronder het IP-adres in.</Empty>)}
      <form className="form-grid" onSubmit={submit}>
        <label className="field">{hostLabel}<input value={host} inputMode="decimal" autoComplete="off" onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.50" required /></label>
        <label className="field">Naam (optioneel)<input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} /></label>
        <ActionButton type="submit" className="btn primary" disabled={pair.isPending}>Koppelen</ActionButton>
      </form>
      {action && <ActionSteps action={action} busy={pair.isPending} onRetry={() => pair.mutate(lastHost)} />}
      {paired && (
        <div className="note ok" role="status">
          {`Gekoppeld: ${paired.name}`}
          {paired.repaired ? ' — bestaande koppeling bijgewerkt; de geschiedenis blijft.' : ''}
          {paired.compatibility === 'unsupported' ? ' Dit model wordt (nog) niet ondersteund; de diagnose is bij het apparaat bewaard.' : ''}
        </div>
      )}
      {(discover.error || pair.error) && <ErrorNote error={discover.error ?? pair.error} />}
      {pair.error && <PairDiagnostics error={pair.error} provider={id} host={lastHost} />}
    </article>
  );
}

const JOIN_SECONDS = 120;

function NewSensorRow({ d, rooms }: { d: { id: string; name: string; kind: string; model: string | null; stable_identity: string; room: string | null }; rooms: Room[] }) {
  const qc = useQueryClient();
  const current = rooms.find((r) => r.name === d.room)?.id ?? '';
  const m = useMutation({
    mutationFn: (room_id: string) => api.patchDevice(d.id, { room_id: room_id || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  });
  return (
    <li>
      <span>
        <span className="pill info">Nieuw gevonden</span> <strong>{d.name}</strong>
        <span className="small muted"> · {d.kind === 'zigbee_device' ? 'type wordt nog bepaald' : t(KIND_LABEL, d.kind)}{d.model ? ` · ${d.model}` : ''}</span>
      </span>
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

/** Zigbee: set up the internal Zigbee2MQTT, open a 2-minute pairing window and show new sensors (item 8). */
export function ZigbeeCard({ homeId }: { homeId: string }) {
  const qc = useQueryClient();
  const instr = useQuery({ queryKey: qk.instructions('zigbee'), queryFn: () => api.integrationInstructions('zigbee') });
  const integrations = useQuery({ queryKey: qk.integrations, queryFn: () => api.integrations() });
  const rooms = useQuery({ queryKey: qk.rooms(homeId), queryFn: () => api.rooms(homeId) });
  const [openUntil, setOpenUntil] = useState<number | null>(null);
  const [known, setKnown] = useState<Set<string> | null>(null);
  const [now, setNow] = useState(Date.now());
  const remaining = openUntil ? Math.max(0, Math.ceil((openUntil - now) / 1000)) : 0;
  const isOpen = remaining > 0;
  const devices = useQuery({ queryKey: qk.devices(homeId), queryFn: () => api.devices(homeId), refetchInterval: isOpen ? 5000 : 60_000 });
  const list = Array.isArray(devices.data) ? devices.data : [];
  useEffect(() => {
    if (!openUntil) return;
    const tm = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tm);
  }, [openUntil]);
  const configured = (Array.isArray(integrations.data) ? integrations.data : []).some((i) => i.provider === 'zigbee' && i.id);
  const setup = useMutation({
    mutationFn: () => api.zigbeeSetup(homeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.integrations });
      qc.invalidateQueries({ queryKey: ['devices'] });
    },
  });
  const join = useMutation({
    mutationFn: () => api.zigbeePermitJoin(homeId, JOIN_SECONDS),
    onSuccess: (r) => {
      setKnown(new Set(list.map((d) => d.id)));
      setNow(Date.now());
      setOpenUntil(new Date(r.open_until).getTime());
    },
  });
  const fresh = known ? list.filter((d) => d.provider === 'zigbee' && d.kind !== 'zigbee_bridge' && !known.has(d.id)) : [];
  const mmss = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
  const ins = instr.data && !Array.isArray(instr.data) ? instr.data : null;
  return (
    <article className="card" aria-labelledby="p-zigbee">
      <h3 id="p-zigbee">Zigbee · Sensor koppelen</h3>
      {instr.isLoading && <Loading />}
      {instr.error && <ErrorNote error={instr.error} />}
      {ins && (
        <>
          <ol className="steps-list">{(ins.steps ?? []).map((s, i) => <li key={i}>{s}</li>)}</ol>
          {ins.needs_manufacturer_app && <p className="small muted" style={{ margin: 0 }}>{ins.needs_manufacturer_app}</p>}
        </>
      )}
      <div className="row" style={{ gap: 8 }}>
        {!configured ? (
          <ActionButton className="btn primary" disabled={setup.isPending || integrations.isLoading} onClick={() => setup.mutate()}>{setup.isPending ? 'Bezig…' : 'Zigbee instellen'}</ActionButton>
        ) : (
          <ActionButton className="btn primary" disabled={join.isPending || isOpen} onClick={() => join.mutate()}>Koppelen starten (2 min)</ActionButton>
        )}
        {isOpen && <span className="small" role="timer" aria-live="off" data-testid="join-countdown">Koppelvenster open · nog {mmss}</span>}
        {openUntil && !isOpen && <span className="small muted">Koppelvenster gesloten.</span>}
      </div>
      {isOpen && <p className="small muted" style={{ margin: 0 }} role="status">Houd nu de koppelknop van de sensor ingedrukt tot het lampje knippert. Nieuwe sensoren verschijnen hieronder.</p>}
      {(setup.error || join.error) && <ErrorNote error={setup.error ?? join.error} />}
      {known && (fresh.length ? (
        <ul className="meter-list" aria-label="Nieuwe sensoren">
          {fresh.map((d) => <NewSensorRow key={d.id} d={d} rooms={Array.isArray(rooms.data) ? rooms.data : []} />)}
        </ul>
      ) : isOpen ? <span className="small muted">Zoeken naar nieuwe sensoren…</span> : null)}
    </article>
  );
}
