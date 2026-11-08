import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { LightGroup, Scene } from '../api/types';
import { useHome, usePending, useCanAct } from '../state';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';

const MODE: Record<string, string> = { lights: 'Alleen verlichting', climate: 'Alleen airco', combined: 'Gecombineerd' };

function GroupCard({ group, scenes }: { group: LightGroup; scenes: Scene[] }) {
  const canAct = useCanAct();
  const { pending, markSent, confirm } = usePending();
  const key = `group:${group.id}`;
  const [bri, setBri] = useState(group.brightness ?? 0);
  useEffect(() => setBri(group.brightness ?? 0), [group.brightness]);
  const m = useMutation({
    mutationFn: (body: { on?: boolean; brightness?: number; scene_id?: string }) => api.setGroup(group.id, body),
    onMutate: () => markSent(key, group.name),
    onError: () => confirm(key),
  });
  const disabled = !canAct || !group.reachable;
  return (
    <article className={`card live`} aria-labelledby={`g-${group.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <div>
          <h3 id={`g-${group.id}`}>{group.name}</h3>
          <span className="card-sub">{group.reachable ? `${group.lights_on} van ${group.lights_total} aan${group.scene ? ` · ${group.scene}` : ''}` : 'Niet bereikbaar'}</span>
        </div>
        <ActionButton className={`btn${group.on ? ' primary' : ''}`} aria-pressed={group.on} disabled={!group.reachable} onClick={() => m.mutate({ on: !group.on })}>
          {group.on ? 'Aan' : 'Uit'}
        </ActionButton>
      </div>
      <label className="field">
        <span className="row" style={{ justifyContent: 'space-between' }}><span>Helderheid</span><span className="muted">{bri}%</span></span>
        <input type="range" min={1} max={100} value={bri || 1} disabled={disabled} onChange={(e) => setBri(Number(e.target.value))} onPointerUp={() => m.mutate({ brightness: bri })} onKeyUp={() => m.mutate({ brightness: bri })} style={{ minHeight: 44 }} />
      </label>
      {scenes.length > 0 && (
        <div className="row" style={{ gap: 8 }}>
          {scenes.map((s) => (
            <ActionButton key={s.id} className="btn" aria-pressed={group.scene === s.name} disabled={!group.reachable} onClick={() => m.mutate({ scene_id: s.id })}>{s.name}</ActionButton>
          ))}
        </div>
      )}
      {pending[key] && <span className="small muted" role="status">Verzonden · wacht op bevestiging…</span>}
      {m.error && <ErrorNote error={m.error} />}
    </article>
  );
}

export function LightsPage() {
  const { homeId } = useHome();
  const lights = useQuery({ queryKey: ['lights', homeId], queryFn: () => api.lights(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  const buttons = useQuery({ queryKey: ['buttons', homeId], queryFn: () => api.buttons(homeId!), enabled: !!homeId });
  if (!homeId || lights.isLoading) return <Loading />;
  if (!lights.data) return <ErrorNote error={lights.error} />;
  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Philips Hue</span><h1>Verlichting</h1></div>
      </header>
      {lights.data.groups.length ? (
        <div className="grid-auto">
          {lights.data.groups.map((g) => <GroupCard key={g.id} group={g} scenes={lights.data.scenes.filter((s) => s.group_id === g.id)} />)}
        </div>
      ) : (
        <Empty>Geen Hue-kamers gevonden. Koppel de Hue Bridge via Apparaten.</Empty>
      )}
      <section aria-labelledby="knop-h" className="card">
        <h2 id="knop-h">Fysieke knoppen</h2>
        <p className="small muted" style={{ margin: 0 }}>Bestaande Hue-regels op de bridge blijven werken, ook als deze app uit staat. Een gecombineerde actie (licht én airco) moet bewust gekozen worden.</p>
        {buttons.isLoading && <Loading />}
        {buttons.data?.length === 0 && <Empty>Geen knoppen gevonden.</Empty>}
        {buttons.data?.map((b) => (
          <div key={b.id} style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div className="section-head"><strong>{b.name}</strong><span className="small muted">{b.model}</span></div>
            {b.mapping.length === 0 ? (
              <Empty>Geen eigen koppeling: knop volgt de Hue-bridge.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Gebeurtenis</th><th>Modus</th><th>Actie</th><th>Conflict</th></tr></thead>
                  <tbody>
                    {b.mapping.map((m) => (
                      <tr key={m.id}>
                        <td>{m.event}</td>
                        <td>{MODE[m.mode] ?? m.mode}</td>
                        <td className="small">{Object.entries(m.action).map(([k, v]) => `${k}: ${String(v)}`).join(', ')}</td>
                        <td>{m.conflict ? <span className="error-text">{m.conflict}</span> : <span className="small muted">geen</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </section>
    </>
  );
}
