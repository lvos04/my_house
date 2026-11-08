import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import type { HvacMode, Zone, ZoneCommand } from '../api/types';
import { ago, CONTROL_LABEL, MODE_LABEL, temp, time } from '../format';
import { usePending, useCanAct } from '../state';
import { FlameIcon, SnowIcon } from './Icons';
import { QualityBadge } from './bits';

export function ModePill({ zone }: { zone: Zone }) {
  if (zone.override) return <span className="pill override">Override</span>;
  if (zone.mode === 'heat') return <span className="pill heat"><FlameIcon />Verwarmen</span>;
  if (zone.mode === 'cool') return <span className="pill cool"><SnowIcon />Koelen</span>;
  return <span className="pill neutral">{MODE_LABEL[zone.mode] ?? zone.mode}</span>;
}

/** Sends zone commands and tracks "verzonden" until the server confirms via zone.updated. */
export function useZoneCommand(zone: Zone) {
  const { pending, markSent, confirm } = usePending();
  const key = `zone:${zone.id}`;
  const [error, setError] = useState<string | null>(null);
  const [desired, setDesired] = useState<number | null>(null);
  const p = pending[key];

  // When confirmed (pending cleared), drop the optimistic target.
  useEffect(() => {
    if (!p) setDesired(null);
  }, [p]);

  const m = useMutation({
    mutationFn: (cmd: ZoneCommand) => api.zoneCommand(zone.id, cmd),
    onMutate: (cmd) => {
      setError(null);
      markSent(key, cmd.target_c !== undefined ? `Doel ${temp(cmd.target_c)}` : cmd.mode ? MODE_LABEL[cmd.mode] : 'Commando');
    },
    onError: (e) => {
      confirm(key);
      setDesired(null);
      setError(e instanceof ApiError || e instanceof Error ? e.message : 'Commando mislukt');
    },
  });

  const step = (dir: 1 | -1) => {
    const cur = desired ?? zone.target_c ?? 21;
    const { min_c, max_c, step_c } = zone.capabilities;
    const next = Math.min(max_c, Math.max(min_c, Math.round((cur + dir * step_c) / step_c) * step_c));
    if (next === cur) return;
    setDesired(next);
    m.mutate({ target_c: next });
  };

  return {
    send: (cmd: ZoneCommand) => m.mutate(cmd),
    step,
    target: desired ?? zone.target_c,
    pending: p,
    error,
  };
}

export function PendingLine({ pending, error }: { pending?: { timedOut: boolean; label: string }; error: string | null }) {
  if (error) return <span className="error-text" role="alert">{error}</span>;
  if (!pending) return null;
  return (
    <span className="small muted" role="status">
      {pending.timedOut
        ? `${pending.label}: geen bevestiging van het apparaat ontvangen. Controleer de status.`
        : `${pending.label} verzonden · wacht op bevestiging…`}
    </span>
  );
}

export function measurementLine(zone: Zone): string {
  const t = zone.temperature;
  const parts: string[] = [];
  if (t.stale) parts.push(`Sensor verouderd (${ago(t.at)})`);
  else if (t.at) parts.push(`${ago(t.at)}`);
  if (zone.humidity.value !== null) parts.push(`${Math.round(zone.humidity.value)}% RV`);
  return parts.join(' · ');
}

export function ZoneCard({ zone, tz }: { zone: Zone; tz?: string }) {
  const canAct = useCanAct();
  const cmd = useZoneCommand(zone);
  const t = zone.temperature;
  const controllable = canAct && zone.online && zone.mode !== 'off';
  const bandLeft = zone.target_c !== null ? 50 - 20 : 0;
  const markPos = zone.target_c !== null && t.value !== null
    ? Math.max(2, Math.min(98, 50 + ((t.value - zone.target_c) / Math.max(zone.policy.band_c * 2, 0.5)) * 20))
    : null;

  return (
    <article className="card live" aria-labelledby={`zone-${zone.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <div>
          <h3 id={`zone-${zone.id}`}>{zone.name}</h3>
          <span className="card-sub">
            {zone.room}
            {zone.profile ? ` · profiel ${zone.profile}` : ''}
            {zone.override?.until ? ` · handmatig tot ${time(zone.override.until, tz)}` : ''}
            {!zone.override ? ` · ${CONTROL_LABEL[zone.control]}` : ''}
          </span>
        </div>
        <ModePill zone={zone} />
      </div>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span className={`big-num${t.stale || t.value === null ? ' stale' : ''}`} style={{ fontSize: 56 }}>{temp(t.value)}</span>
          <span className="small" style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', color: t.stale ? 'var(--warn)' : 'var(--muted)' }}>
            <QualityBadge quality={t.quality} />
            {measurementLine(zone)}
          </span>
        </div>
        <div className="setpoint">
          <button type="button" className="icon-btn" aria-label={`Doeltemperatuur ${zone.name} lager`} disabled={!controllable} onClick={() => cmd.step(-1)}>−</button>
          <div className="setpoint-val">
            <span className="small muted">Doel</span>
            <span style={{ fontSize: 22, fontWeight: 600 }} aria-live="polite">{temp(cmd.target)}</span>
          </div>
          <button type="button" className="icon-btn" aria-label={`Doeltemperatuur ${zone.name} hoger`} disabled={!controllable} onClick={() => cmd.step(1)}>+</button>
        </div>
      </div>
      {zone.mode === 'heat' && markPos !== null && (
        <div className="band" aria-hidden="true">
          <div className="band-fill" style={{ left: `${bandLeft}%`, width: '40%' }} />
          <div className="band-mark" style={{ left: `${markPos}%` }} />
        </div>
      )}
      <PendingLine pending={cmd.pending} error={cmd.error} />
      {zone.notice && <div className="note warn">{zone.notice}</div>}
      {!zone.online && <div className="note error">Airco-unit offline. Gebruik de afstandsbediening van de fabrikant.</div>}
      {zone.next_change && !zone.notice && (
        <span className="card-sub">
          Comfortband ±{zone.policy.band_c.toLocaleString('nl-NL')}° · volgende wijziging {time(zone.next_change.at, tz)} naar {zone.next_change.label} ({temp(zone.next_change.target_c)})
        </span>
      )}
    </article>
  );
}

export function ModeButtons({ zone, onPick }: { zone: Zone; onPick: (m: HvacMode) => void }) {
  const canAct = useCanAct();
  const blocked = new Map(zone.blocked_modes.map((b) => [b.mode, b.reason]));
  const modes: HvacMode[] = ['off', ...zone.capabilities.modes.filter((m) => m !== 'off')];
  return (
    <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <legend className="small muted" style={{ marginBottom: 8 }}>Modus</legend>
      <div className="mode-grid">
        {modes.map((m) => {
          const reason = blocked.get(m);
          return (
            <button
              key={m}
              type="button"
              className={`mode-btn ${m}${reason ? ' blocked' : ''}`}
              aria-pressed={zone.mode === m}
              aria-describedby={reason ? `blocked-${zone.id}` : undefined}
              disabled={!canAct || !zone.online || Boolean(reason)}
              onClick={() => onPick(m)}
            >
              {MODE_LABEL[m] ?? m}
            </button>
          );
        })}
      </div>
      {zone.blocked_modes.length > 0 && (
        <div id={`blocked-${zone.id}`} className="note info">
          {zone.blocked_modes.map((b) => (
            <div key={b.mode}><strong>{MODE_LABEL[b.mode]}</strong> niet mogelijk: {b.reason}</div>
          ))}
        </div>
      )}
    </fieldset>
  );
}
