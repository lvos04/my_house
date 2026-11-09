import '../climate/climate.css';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { LightGroup, LivePower, Overview as OverviewT, StatusCheck } from '../api/types';
import { runCommand, useLatestCommand } from '../commands';
import { useHome } from '../state';
import { ActionButton, Dot, Empty, ErrorNote, Loading, QualityBadge } from '../components/bits';
import { ZoneCard } from '../components/zone';
import { MeterList, partMeters, StackBar } from '../components/energy';
import { ago, dateTime, kwh, money, time, watts } from '../format';
import { CHECK_STATE_LABEL, SEVERITY_LABEL, t } from '../i18n';
import { PlusIcon } from '../components/Icons';
import { FormError, useClimateCan } from '../climate/common';

/** Greeting by the hour in the home's time zone. */
export function greeting(d = new Date(), tz?: string) {
  const h = Number(new Intl.DateTimeFormat('en-GB', { hour: '2-digit', hourCycle: 'h23', timeZone: tz }).format(d)) % 24;
  return h < 6 ? 'Goedenacht' : h < 12 ? 'Goedemorgen' : h < 18 ? 'Goedemiddag' : 'Goedenavond';
}

/** Light group tile. Toggling is tracked until the bridge confirms it (command.updated); viewers only see the state. */
export function LightTile({ group }: { group: LightGroup }) {
  const { control } = useClimateCan();
  const key = `group:${group.id}`;
  const cmd = useLatestCommand(key);
  const m = useMutation({
    mutationFn: () => runCommand(api.setGroup(group.id, { on: !group.on }), { key, label: group.on ? 'Uit' : 'Aan' }),
  });
  const state = !group.reachable
    ? 'niet bereikbaar'
    : group.on
      ? [group.scene, group.brightness !== null ? `${group.brightness}%` : null].filter(Boolean).join(' · ') || 'Aan'
      : 'Uit';
  const status = m.isPending || cmd?.busy
    ? `${cmd?.label ?? (group.on ? 'Uit' : 'Aan')} · ${cmd?.phase === 'sent' ? 'verzonden, nog niet bevestigd' : 'wordt verzonden…'}`
    : cmd && (cmd.phase === 'failed' || cmd.phase === 'denied' || cmd.phase === 'timeout')
      ? cmd.text
      : state;
  if (!control) {
    return (
      <div className={`light-tile live${group.on ? ' on' : ''}`} style={{ cursor: 'default' }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{group.name}</span>
        <span className="small muted">{state}</span>
      </div>
    );
  }
  return (
    <ActionButton
      className={`light-tile live${group.on ? ' on' : ''}`}
      aria-pressed={group.on}
      aria-label={`${group.name}: ${status}. Tik om ${group.on ? 'uit' : 'aan'} te zetten`}
      disabled={!group.reachable || m.isPending}
      onClick={() => m.mutate()}
    >
      <span style={{ fontWeight: 600, fontSize: 15 }}>{group.name}</span>
      <span className="small muted">{status}</span>
      {m.error ? <span className="small" style={{ color: 'var(--error)' }}>{m.error instanceof Error ? m.error.message : 'Mislukt'}</span> : null}
    </ActionButton>
  );
}

const ATTENTION_LINK: Record<string, { to: string; label: string }> = {
  worker: { to: '/systeem', label: 'Naar Systeem' },
  safe_mode: { to: '/systeem#veilige-modus', label: 'Veilige modus bekijken' },
  backup: { to: '/systeem#backups', label: 'Naar back-ups' },
  maintenance: { to: '/systeem', label: 'Naar Systeem' },
};

/** Item 22: the worker heartbeat, safe mode and backup age belong on the normal overview too. */
export function attentionItems(status: StatusCheck[]): StatusCheck[] {
  return status.filter((s) => (s.key === 'safe_mode' || (s.key in ATTENTION_LINK && s.state !== 'ok' && s.state !== 'unknown')));
}

function Attention({ status }: { status: StatusCheck[] }) {
  const { admin } = useClimateCan();
  const items = attentionItems(status);
  if (!items.length) return null;
  const serious = items.some((s) => s.state === 'error');
  return (
    <section className="card attention c-attention" aria-labelledby="attn-h">
      <h2 id="attn-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>{serious ? 'Er is een probleem met het systeem' : 'Let op'}</h2>
      <ul>
        {items.map((s) => {
          const link = ATTENTION_LINK[s.key];
          return (
            <li key={s.key}>
              <Dot state={s.state} />
              <span>
                <strong>{s.label}</strong><span className="sr-only"> ({t(CHECK_STATE_LABEL, s.state)})</span>: {s.detail}
                {admin && link && <> · <Link to={link.to}>{link.label}</Link></>}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function PowerNow({ p, tz }: { p: LivePower; tz?: string }) {
  const stale = p.parts.filter((x) => x.stale).map((x) => x.label);
  return (
    <div className="stack tight">
      <span className="small row" style={{ gap: 8 }}>
        <span>Nu <strong>{watts(p.total_w)}</strong></span>
        <QualityBadge quality={p.total_w === null ? 'missing' : p.complete ? 'measured' : 'estimated'} />
        <span className="muted">om {time(p.at, tz)} ({ago(p.at)})</span>
      </span>
      {(p.note || stale.length > 0) && (
        <span className="small muted">{p.note ?? `Geen actuele meting van: ${stale.join(', ')}.`}</span>
      )}
    </div>
  );
}

export function OverviewView({ data }: { data: OverviewT }) {
  const qc = useQueryClient();
  const { control, admin } = useClimateCan();
  const tz = data.home.timezone;
  const e = data.energy_today;
  const parts = partMeters(e);
  // "Alle lampen" contains every lamp again: leave it out of the count unless it is the only group.
  const counted = data.lights.some((g) => !g.all_lights) ? data.lights.filter((g) => !g.all_lights) : data.lights;
  const lightsOn = counted.reduce((a, g) => a + g.lights_on, 0);
  const lightsTotal = counted.reduce((a, g) => a + g.lights_total, 0);
  // Live power and alerts are kept current by ws.ts (energy.live patches power_now, alert.resolved
  // removes alerts), so this view only renders the overview cache.
  const power = data.power_now;
  const ack = useMutation({
    mutationFn: api.ackAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.overview(data.home.id) }),
  });
  const visibleLights = data.lights.filter((g) => !g.hidden);
  const attention = attentionItems(data.status); // shown at the top, not repeated in the grid

  return (
    <>
      <header className="page-head">
        <div>
          <span className="eyebrow">{new Date().toLocaleString('nl-NL', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: tz })} · {data.home.name}</span>
          <h1>{greeting(new Date(), tz)}</h1>
        </div>
        {admin && <Link to="/apparaten" className="btn"><PlusIcon />Apparaat toevoegen</Link>}
      </header>

      <Attention status={data.status} />

      {data.status.length > attention.length && (
        <section aria-label="Systeemstatus" className="grid-status wide live">
          {data.status.filter((s) => !attention.includes(s)).map((s) => (
            <div key={s.key} className="card tight">
              <span className="card-sub">{s.label}</span>
              <span className={`state-line ${s.state}`}>
                <Dot state={s.state} />
                <span><span className="sr-only">{t(CHECK_STATE_LABEL, s.state)}: </span>{s.detail}</span>
              </span>
            </div>
          ))}
        </section>
      )}

      <section aria-labelledby="klimaat-h" className="section">
        <div className="section-head">
          <h2 id="klimaat-h">Klimaat</h2>
          <Link to="/klimaat" className="small" style={{ fontWeight: 500 }}>Alle instellingen</Link>
        </div>
        {data.zones.length ? (
          <div className="c-grid">{data.zones.map((z) => <ZoneCard key={z.id} zone={z} tz={tz} />)}</div>
        ) : (
          <Empty>Nog geen klimaatzones. Koppel de airco-units via Apparaten.</Empty>
        )}
      </section>

      <div className="c-grid">
        <section aria-labelledby="licht-h" className="card">
          <div className="section-head">
            <h2 id="licht-h">Verlichting</h2>
            <span className="small muted">{lightsOn} van {lightsTotal} aan</span>
          </div>
          {visibleLights.length ? (
            <div className="grid-2">{visibleLights.map((g) => <LightTile key={g.id} group={g} />)}</div>
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
          {e.cost_note && <span className="small muted">{e.cost_note}</span>}
          {power && <PowerNow p={power} tz={tz} />}
        </section>

        <section aria-labelledby="melding-h" className="card">
          <h2 id="melding-h">Meldingen</h2>
          {data.alerts.length === 0 && <Empty>Geen open meldingen.</Empty>}
          {data.alerts.slice(0, 6).map((a) => (
            <div key={a.id} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}>
              <span style={{ marginTop: 7 }}><Dot state={a.severity} /></span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 0 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}><span className="sr-only">{t(SEVERITY_LABEL, a.severity)}: </span>{a.title}</span>
                <span className="small muted">{dateTime(a.created_at, tz)} · {a.detail}</span>
              </div>
              {control && !a.acknowledged && (
                <ActionButton className="btn small" aria-label={`Melding ${a.title} afhandelen`} onClick={() => ack.mutate(a.id)}>
                  Gezien
                </ActionButton>
              )}
            </div>
          ))}
          {data.alerts.length > 6 && <span className="small muted">En nog {data.alerts.length - 6} meldingen.</span>}
          <FormError error={ack.error} />
        </section>
      </div>
    </>
  );
}

export function OverviewPage() {
  const { homeId } = useHome();
  const q = useQuery({ queryKey: qk.overview(homeId ?? ''), queryFn: () => api.overview(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  if (!homeId || q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  return <OverviewView data={q.data} />;
}
