import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Device, DiscoveredDevice, PairingInstructions } from '../api/types';
import { useHome } from '../state';
import { useIsAdmin } from '../user';
import { ActionButton, Dot, Empty, ErrorNote, Loading } from '../components/bits';
import { ago } from '../format';

const PROVIDERS: { id: string; label: string; hostLabel: string; help: string }[] = [
  { id: 'hue', label: 'Philips Hue', hostLabel: 'IP-adres Hue Bridge', help: 'Druk op de ronde link-knop bovenop de Hue Bridge en klik binnen 30 seconden op Koppelen.' },
  { id: 'homewizard', label: 'HomeWizard', hostLabel: 'IP-adres P1-meter, Energy Socket of kWh-meter', help: 'Zet in de HomeWizard Energy-app per apparaat "Lokale API" aan (Instellingen › Meters › apparaat). Nieuwere apparaten vragen om een druk op de knop.' },
  { id: 'gree', label: 'Gree airco', hostLabel: 'IP-adres binnenunit', help: 'Koppel elke binnenunit eerst via de Gree+-app aan je 2,4 GHz-wifi. Daarna zoekt deze app de unit op het lokale netwerk. Niet elk model ondersteunt lokale bediening; onbekende modellen krijgen de status "niet getest".' },
  { id: 'zigbee', label: 'Zigbee', hostLabel: '', help: 'Start pairing en houd de knop van de sensor ingedrukt tot het lampje knippert (Aqara: ±5 s). Het pairingvenster staat 4 minuten open.' },
];

const COMPAT: Record<Device['compatibility'], string> = { tested: 'getest', untested: 'nog niet getest', unsupported: 'niet ondersteund' };

function isInstructions(x: unknown): x is PairingInstructions {
  return typeof x === 'object' && x !== null && 'steps' in x;
}

function ProviderCard({ id, label, hostLabel, help }: (typeof PROVIDERS)[number]) {
  const qc = useQueryClient();
  const [host, setHost] = useState('');
  const [found, setFound] = useState<DiscoveredDevice[] | null>(null);
  const [instructions, setInstructions] = useState<PairingInstructions | null>(null);
  const [paired, setPaired] = useState<string | null>(null);
  const discover = useMutation({ mutationFn: () => api.discover(id), onSuccess: setFound });
  const pair = useMutation({
    mutationFn: (h: string) => api.pair(id, h ? { host: h } : {}),
    onSuccess: (r) => {
      if (isInstructions(r)) {
        setInstructions(r);
        setPaired(null);
      } else {
        setInstructions(null);
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
        <ActionButton className="btn" disabled={discover.isPending} onClick={() => discover.mutate()}>{discover.isPending ? 'Zoeken…' : id === 'zigbee' ? 'Pairing starten' : 'Zoeken op netwerk'}</ActionButton>
      </div>
      {found && (found.length ? (
        <ul className="meter-list">
          {found.map((d) => (
            <li key={d.stable_identity}>
              <span>{d.name}<span className="small muted"> · {d.model ?? d.kind}{d.host ? ` · ${d.host}` : ''}</span></span>
              <ActionButton className="btn" disabled={pair.isPending} onClick={() => pair.mutate(d.host ?? '')}>Koppelen</ActionButton>
            </li>
          ))}
        </ul>
      ) : <Empty>Niets gevonden. Vul hieronder het IP-adres in.</Empty>)}
      {hostLabel && (
        <form className="form-grid" onSubmit={submit}>
          <label className="field">{hostLabel}<input value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.50" inputMode="decimal" required /></label>
          <ActionButton type="submit" className="btn primary" disabled={pair.isPending}>Koppelen</ActionButton>
        </form>
      )}
      {instructions && (
        <div className="note info" role="status">
          <ol style={{ margin: 0, paddingLeft: 18 }}>{instructions.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
          {instructions.retry_after_s ? <div style={{ marginTop: 6 }}>Probeer het daarna opnieuw (na ±{instructions.retry_after_s} s).</div> : null}
        </div>
      )}
      {paired && <div className="note info" role="status">Gekoppeld: {paired}</div>}
      {(discover.error || pair.error) && <ErrorNote error={discover.error ?? pair.error} />}
    </article>
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
            <div key={i.provider} className="card tight live">
              <span className="card-sub">{i.label}</span>
              <span className={`state-line ${i.state}`}><Dot state={i.state} />{i.state === 'not_configured' ? 'Niet ingesteld' : i.detail}</span>
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
              <thead><tr><th>Naam</th><th>Merk</th><th>Ruimte</th><th>Rol</th><th>Status</th><th>Compatibiliteit</th></tr></thead>
              <tbody>
                {devices.data.map((d) => (
                  <tr key={d.id}>
                    <td><strong>{d.name}</strong><div className="small muted">{d.kind} · {d.stable_identity}</div></td>
                    <td>{d.provider}</td>
                    <td>{d.room ?? '–'}</td>
                    <td>{d.roles.join(', ') || '–'}{d.protected && <div className="small" style={{ color: 'var(--warn)' }}>beschermd: alleen meten</div>}</td>
                    <td><span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}><Dot state={d.online ? 'ok' : 'error'} />{d.online ? 'online' : `offline · ${ago(d.last_seen)}`}</span></td>
                    <td>{COMPAT[d.compatibility]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {isAdmin && (
        <section aria-labelledby="add-h" className="section">
          <h2 id="add-h">Apparaat toevoegen</h2>
          <div className="grid-auto">{PROVIDERS.map((p) => <ProviderCard key={p.id} {...p} />)}</div>
        </section>
      )}
    </>
  );
}
