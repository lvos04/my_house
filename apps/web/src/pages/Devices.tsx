import '../devices/devices.css';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Device } from '../api/types';
import { useHome } from '../state';
import { useCan } from '../user';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';
import { ago } from '../format';
import { DeviceCard } from '../devices/DeviceCard';
import { IntegrationsSection } from '../devices/Integrations';
import { CompatibilitySection } from '../devices/Compatibility';
import { PROVIDERS, ProviderCard, ZigbeeCard } from '../devices/Pairing';
import { CATEGORY_LABEL, CATEGORY_ORDER, deviceCategory, kindLabel, type Category } from '../devices/labels';

export { PROTECTED_TEXT } from '../devices/DeviceCard';

function ArchivedDevices({ homeId, admin }: { homeId: string; admin: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: [...qk.devices(homeId), 'archived'], queryFn: () => api.devices(homeId, { includeArchived: true }), enabled: open });
  const archived = (Array.isArray(q.data) ? q.data : []).filter((d) => d.lifecycle === 'archived');
  const restore = useMutation({
    mutationFn: (id: string) => api.restoreDevice(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.devices(homeId) }),
  });
  return (
    <details className="card dv-archived" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary><h2 style={{ display: 'inline', fontSize: 18 }}>Gearchiveerde apparaten</h2></summary>
      {q.isLoading && <Loading />}
      {q.error && <ErrorNote error={q.error} />}
      {q.isSuccess && archived.length === 0 && <Empty>Geen gearchiveerde apparaten.</Empty>}
      {archived.length > 0 && (
        <ul className="meter-list">
          {archived.map((d) => (
            <li key={d.id}>
              <span>{d.name}<span className="small muted"> · {kindLabel(d.kind)}{d.last_seen ? ` · laatst gezien ${ago(d.last_seen)}` : ''}</span></span>
              {admin && (
                <ActionButton className="btn small" disabled={restore.isPending} onClick={() => restore.mutate(d.id)} aria-label={`${d.name} terugzetten`}>Terugzetten</ActionButton>
              )}
            </li>
          ))}
        </ul>
      )}
      {restore.error && <ErrorNote error={restore.error} />}
      <p className="small muted" style={{ margin: 0 }}>Na terugzetten moet je rollen en zonekoppelingen opnieuw kiezen.</p>
    </details>
  );
}

export function DevicesPage() {
  const { homeId } = useHome();
  const { admin } = useCan();
  const [notice, setNotice] = useState<string | null>(null);
  const devices = useQuery({ queryKey: qk.devices(homeId ?? ''), queryFn: () => api.devices(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  const rooms = useQuery({ queryKey: qk.rooms(homeId ?? ''), queryFn: () => api.rooms(homeId!), enabled: !!homeId && admin });
  const list: Device[] = Array.isArray(devices.data) ? devices.data : [];
  const roomList = Array.isArray(rooms.data) ? rooms.data : [];
  const byCat = new Map<Category, Device[]>();
  for (const d of list) byCat.set(deviceCategory(d.kind), [...(byCat.get(deviceCategory(d.kind)) ?? []), d]);
  const card = (d: Device) => <DeviceCard key={d.id} d={d} all={list} homeId={homeId!} admin={admin} rooms={roomList} onNotice={setNotice} />;

  return (
    <>
      <header className="page-head"><div><span className="eyebrow">Installatie</span><h1>Apparaten</h1></div></header>
      <IntegrationsSection admin={admin} />
      <section aria-labelledby="dev-h" className="section">
        <h2 id="dev-h">Gekoppelde apparaten</h2>
        {notice && (
          <div className="note ok row" role="status" style={{ justifyContent: 'space-between' }}>
            <span>{notice}</span>
            <button type="button" className="btn small ghost" onClick={() => setNotice(null)}>Sluiten</button>
          </div>
        )}
        {devices.isLoading && <Loading />}
        {devices.error && <ErrorNote error={devices.error} />}
        {devices.isSuccess && list.length === 0 && <Empty>Nog geen apparaten gekoppeld.</Empty>}
        {CATEGORY_ORDER.filter((c) => byCat.has(c)).map((c) => {
          const items = byCat.get(c)!;
          // Lamps, groups and scenes are managed on the lights page: listed here, folded away.
          if (c === 'lights') {
            return (
              <details key={c} className="card dv-cat">
                <summary><h3 className="dv-cat-h">{CATEGORY_LABEL[c]} ({items.length})</h3></summary>
                <div className="dv-list">{items.map(card)}</div>
              </details>
            );
          }
          return (
            <section key={c} className="dv-cat" aria-labelledby={`cat-${c}`}>
              <h3 id={`cat-${c}`} className="dv-cat-h">{CATEGORY_LABEL[c]}</h3>
              <div className="dv-list">{items.map(card)}</div>
            </section>
          );
        })}
        {homeId && <ArchivedDevices homeId={homeId} admin={admin} />}
      </section>
      <CompatibilitySection />
      {admin && homeId && (
        <section aria-labelledby="add-h" className="section">
          <h2 id="add-h">Apparaat toevoegen</h2>
          <p className="small muted" style={{ margin: 0 }}>Koppel je een apparaat opnieuw (bijvoorbeeld na een storing), dan wordt de bestaande koppeling bijgewerkt en blijft de geschiedenis bewaard.</p>
          <div className="grid-auto">
            {PROVIDERS.map((p) => <ProviderCard key={p.id} {...p} homeId={homeId} />)}
            <ZigbeeCard homeId={homeId} />
          </div>
        </section>
      )}
    </>
  );
}
