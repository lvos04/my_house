import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { LightGroup, Overview as OverviewT } from '../api/types';
import { useHome, usePending } from '../state';
import { ActionButton, Dot, Empty, ErrorNote, Loading } from '../components/bits';
import { ZoneCard } from '../components/zone';
import { MeterList, partMeters, StackBar } from '../components/energy';
import { dateTime, kwh, money, watts } from '../format';
import { PlusIcon } from '../components/Icons';

function greeting(d = new Date()) {
  const h = d.getHours();
  return h < 6 ? 'Goedenacht' : h < 12 ? 'Goedemorgen' : h < 18 ? 'Goedemiddag' : 'Goedenavond';
}

export function LightTile({ group }: { group: LightGroup }) {
  const { pending, markSent, confirm } = usePending();
  const key = `group:${group.id}`;
  const m = useMutation({
    mutationFn: () => api.setGroup(group.id, { on: !group.on }),
    onMutate: () => markSent(key, group.on ? 'Uit' : 'Aan'),
    onError: () => confirm(key),
  });
  const status = pending[key]
    ? 'verzonden…'
    : !group.reachable
      ? 'niet bereikbaar'
      : group.on
        ? [group.scene, group.brightness !== null ? `${group.brightness}%` : null].filter(Boolean).join(' · ') || 'Aan'
        : 'Uit';
  return (
    <ActionButton
      className={`light-tile live${group.on ? ' on' : ''}`}
      aria-pressed={group.on}
      aria-label={`${group.name}: ${status}. Tik om ${group.on ? 'uit' : 'aan'} te zetten`}
      disabled={!group.reachable}
      onClick={() => m.mutate()}
    >
      <span style={{ fontWeight: 600, fontSize: 15 }}>{group.name}</span>
      <span className="small muted">{status}</span>
    </ActionButton>
  );
}

export function OverviewView({ data }: { data: OverviewT }) {
  const qc = useQueryClient();
  const tz = data.home.timezone;
  const e = data.energy_today;
  const parts = partMeters(e);
  const lightsOn = data.lights.reduce((a, g) => a + g.lights_on, 0);
  const lightsTotal = data.lights.reduce((a, g) => a + g.lights_total, 0);
  const ack = useMutation({
    mutationFn: api.ackAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['overview', data.home.id] }),
  });

  return (
    <>
      <header className="page-head">
        <div>
          <span className="eyebrow">{new Date().toLocaleString('nl-NL', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: tz })} · {data.home.name}</span>
          <h1>{greeting()}</h1>
        </div>
        <Link to="/apparaten" className="btn"><PlusIcon />Apparaat toevoegen</Link>
      </header>

      <section aria-label="Systeemstatus" className="grid-status live">
        {data.status.map((s) => (
          <div key={s.key} className="card tight">
            <span className="card-sub">{s.label}</span>
            <span className={`state-line ${s.state}`}><Dot state={s.state} />{s.detail}</span>
          </div>
        ))}
      </section>

      <section aria-labelledby="klimaat-h" className="section">
        <div className="section-head">
          <h2 id="klimaat-h">Klimaat</h2>
          <Link to="/klimaat" className="small" style={{ fontWeight: 500 }}>Alle instellingen</Link>
        </div>
        {data.zones.length ? (
          <div className="grid-auto">{data.zones.map((z) => <ZoneCard key={z.id} zone={z} tz={tz} />)}</div>
        ) : (
          <Empty>Nog geen klimaatzones. Koppel de airco-units via Apparaten.</Empty>
        )}
      </section>

      <div className="grid-auto">
        <section aria-labelledby="licht-h" className="card">
          <div className="section-head">
            <h2 id="licht-h">Verlichting</h2>
            <span className="small muted">{lightsOn} van {lightsTotal} aan</span>
          </div>
          {data.lights.length ? (
            <div className="grid-2">{data.lights.map((g) => <LightTile key={g.id} group={g} />)}</div>
          ) : (
            <Empty>Geen Hue Bridge gekoppeld.</Empty>
          )}
        </section>

        <section aria-labelledby="energie-h" className="card live">
          <div className="section-head">
            <h2 id="energie-h">Vandaag</h2>
            <Link to="/energie" className="small" style={{ fontWeight: 500 }}>Energie</Link>
          </div>
          <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
            <span className="big-num" style={{ fontSize: 40 }}>{kwh(e.total_kwh)}</span>
            {e.cost !== null && <span className="muted">{money(e.cost, e.currency)}</span>}
          </div>
          <StackBar meters={parts} />
          <MeterList meters={parts} currency={e.currency} />
          {data.power_now && <span className="small muted">Nu {watts(data.power_now.total_w)}</span>}
        </section>

        <section aria-labelledby="melding-h" className="card">
          <h2 id="melding-h">Meldingen</h2>
          {data.alerts.length === 0 && <Empty>Geen open meldingen.</Empty>}
          {data.alerts.slice(0, 6).map((a) => (
            <div key={a.id} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}>
              <span style={{ marginTop: 7 }}><Dot state={a.severity} /></span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{a.title}</span>
                <span className="small muted">{dateTime(a.created_at, tz)} · {a.detail}</span>
              </div>
              {!a.acknowledged && (
                <ActionButton className="btn" style={{ minHeight: 36, padding: '0 10px', fontSize: 13 }} aria-label={`Melding ${a.title} afhandelen`} onClick={() => ack.mutate(a.id)}>
                  Gezien
                </ActionButton>
              )}
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

export function OverviewPage() {
  const { homeId } = useHome();
  const q = useQuery({ queryKey: ['overview', homeId], queryFn: () => api.overview(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  if (!homeId || q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  return <OverviewView data={q.data} />;
}
