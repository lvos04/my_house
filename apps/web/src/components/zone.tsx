// Zone controls shared by Overzicht (ZoneCard) and Klimaat (ZoneControlPanel).
//
// Commands (verbeterlijst 23): +/− presses are combined (≈800 ms) into one final value; the shown target
// stays the requested value marked "wordt verzonden…/verzonden" until the server reports the command
// as confirmed via WS command.updated. A zone.updated alone is never a confirmation. Failed/denied
// commands show the server's Dutch reason. Item 13: +/−, fan and "Handmatig 2 uur" are disabled while
// the unit is off or offline (setpoint_adjustable=false) with "Kies eerst een modus". Item 11:
// "Terug naar schema" follows can_resume (also from "uit"). Item 31: a zone without airco is neutral.
import '../climate/climate.css';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, isApiError } from '../api/client';
import type { FanSpeed, HvacMode, Zone, ZoneCommand } from '../api/types';
import { applyCommandSnapshot, runCommand, useLatestCommand, type CommandView } from '../commands';
import { useDebouncedSetpoint, type DebouncedSetpoint } from '../setpoint';
import { ago, MODE_LABEL, temp, time } from '../format';
import { FAN_LABEL, OVERRIDE_SOURCE_LABEL, t } from '../i18n';
import { useCanAct, useHome } from '../state';
import { FlameIcon, SnowIcon } from './Icons';
import { ActionButton, QualityBadge } from './bits';
import { durationText, FormError, SafeLink, storeZone, useClimateCan } from '../climate/common';

/* ---------- texts ---------- */

/** How the zone is regulated now, in plain Dutch. */
export function controlText(zone: Zone, tz?: string): string {
  if (!zone.unit_linked) return 'Geen airco gekoppeld';
  switch (zone.control) {
    case 'manual': {
      const until = zone.override?.until ? ` tot ${time(zone.override.until, tz)}` : '';
      const src = zone.override?.source ? ` ${t(OVERRIDE_SOURCE_LABEL, zone.override.source, '')}` : '';
      return `Handmatig${until}${src}`.trim();
    }
    case 'off':
      return 'Uitgezet, schema gepauzeerd';
    case 'oem_fallback':
      return 'Volgt het schema (airco regelt op eigen sensor)';
    default:
      return 'Volgt het schema';
  }
}

export function ModePill({ zone }: { zone: Zone }) {
  if (!zone.unit_linked) return <span className="pill neutral">Geen airco</span>;
  if (zone.unit_status === 'offline') return <span className="pill warn">Niet bereikbaar</span>;
  if (zone.control === 'manual') return <span className="pill override">Handmatig</span>;
  if (zone.mode === 'heat') return <span className="pill heat"><FlameIcon />Verwarmen</span>;
  if (zone.mode === 'cool') return <span className="pill cool"><SnowIcon />Koelen</span>;
  return <span className="pill neutral">{MODE_LABEL[zone.mode] ?? zone.mode}</span>;
}

export function measurementLine(zone: Zone): string {
  const tm = zone.temperature;
  const parts: string[] = [];
  if (tm.stale) parts.push(`Sensor verouderd (${ago(tm.at)})`);
  else if (tm.at) parts.push(`${ago(tm.at)}`);
  if (zone.humidity.value !== null) parts.push(`${Math.round(zone.humidity.value)}% RV`);
  return parts.join(' · ');
}

/** Why +/−, fan and "Handmatig" are unavailable, or null when they are available. */
export function disabledReason(zone: Zone): string | null {
  if (!zone.unit_linked) return null;
  if (zone.unit_status === 'offline') return 'De airco is niet bereikbaar. Bedienen kan pas weer als hij reageert; de afstandsbediening werkt wel.';
  if (!zone.setpoint_adjustable) return 'Kies eerst een modus. De airco staat uit; temperatuur, ventilator en handmatig bijstellen werken pas als hij aan staat.';
  return null;
}

/* ---------- commands ---------- */

export interface ZoneControl {
  sp: DebouncedSetpoint;
  /** Newest command for this zone (from this tab, another device or the engine). */
  latest: CommandView | null;
  error: unknown;
  /** +/−, fan and "Handmatig" may be used. */
  adjustable: boolean;
  /** A mode may be chosen (unit linked, reachable, app online). */
  modesEnabled: boolean;
  bump: (dir: 1 | -1) => void;
  sendMode: (m: HvacMode) => void;
  sendFan: (f: FanSpeed) => void;
  sendOverride: () => void;
  overrideMinutes: number;
}

/** Commands for one zone, tracked until the server confirms them. */
export function useZoneControl(zone: Zone): ZoneControl {
  const canAct = useCanAct();
  const key = `zone:${zone.id}`;
  const latest = useLatestCommand(key);
  const [error, setError] = useState<unknown>(null);
  const { min_c, max_c, step_c } = zone.capabilities;
  const sp = useDebouncedSetpoint({
    value: zone.target_c,
    min: min_c,
    max: max_c,
    step: step_c > 0 ? step_c : 1,
    fallback: 21,
    onSend: (target) => runCommand(api.zoneCommand(zone.id, { target_c: target }), { key, label: `Doeltemperatuur ${temp(target)}` }),
  });
  useEffect(() => {
    if (sp.error) setError(sp.error);
  }, [sp.error]);

  // After a reload the command store is empty: take the zone's command in flight from the REST data.
  const pc = zone.pending_command;
  useEffect(() => {
    if (!pc) return;
    const since = pc.since ? Date.parse(pc.since) : NaN;
    applyCommandSnapshot(key, pc, Number.isFinite(since) ? Math.min(Date.now(), since) : Date.now());
  }, [key, pc?.id, pc?.status]);

  const send = useCallback(
    (cmd: ZoneCommand, label: string) => {
      setError(null);
      runCommand(api.zoneCommand(zone.id, cmd), { key, label }).catch(setError);
    },
    [zone.id, key],
  );
  const overrideMinutes = zone.policy.override_default_minutes || 120;
  const adjustable = canAct && zone.unit_linked && zone.setpoint_adjustable;
  return {
    sp,
    latest,
    error,
    adjustable,
    modesEnabled: canAct && zone.unit_linked && zone.unit_status === 'online',
    bump: (dir) => {
      setError(null);
      sp.bump(dir);
    },
    sendMode: (m) => send({ mode: m }, `Modus ${(MODE_LABEL[m] ?? m).toLowerCase()}`),
    sendFan: (f) => send({ fan: f }, `Ventilator ${t(FAN_LABEL, f).toLowerCase()}`),
    sendOverride: () => {
      const target = sp.value ?? zone.target_c;
      sp.reset();
      send(
        target !== null ? { target_c: target, override_minutes: overrideMinutes } : { override_minutes: overrideMinutes },
        `Handmatig ${durationText(overrideMinutes)}${target !== null ? ` op ${temp(target)}` : ''}`,
      );
    },
    overrideMinutes,
  };
}

/** Hide a confirmed/cancelled line after a few seconds; keep failures visible. */
function useVisible(view: CommandView | null, ms = 6000): boolean {
  const [hidden, setHidden] = useState<string | null>(null);
  const tag = view ? `${view.id}:${view.phase}` : null;
  useEffect(() => {
    if (!view || (view.phase !== 'confirmed' && view.phase !== 'cancelled')) return;
    const tm = setTimeout(() => setHidden(`${view.id}:${view.phase}`), ms);
    return () => clearTimeout(tm);
  }, [tag]);
  return Boolean(view) && hidden !== tag;
}

/** Status of the zone's newest command and the last error. Always rendered as a polite live region,
 * so screen readers hear "verzonden", "bevestigd" or the reason it failed. */
export function CommandLine({ ctl }: { ctl: ZoneControl }) {
  const { sp, latest, error } = ctl;
  const visible = useVisible(latest);
  let content: ReactNode = null;
  if (error) {
    content = isApiError(error, 'mode_required') ? <div className="note warn">{error.message || 'Kies eerst een modus.'}</div> : <FormError error={error} />;
  } else if (sp.waiting && sp.value !== null) {
    content = <span className="c-status sending">Doeltemperatuur {temp(sp.value)} · wordt zo verzonden…</span>;
  } else if (latest && visible) {
    content = (
      <span className={`c-status ${latest.phase}`}>
        <strong>{latest.label ?? 'Opdracht aan de airco'}:</strong> {latest.text}
        {latest.attempts > 1 && !latest.final ? ` (poging ${latest.attempts})` : ''}
      </span>
    );
  }
  return <div aria-live="polite">{content}</div>;
}

/* ---------- pieces ---------- */

export function ZoneNotice({ zone }: { zone: Zone }) {
  if (!zone.notice || !zone.unit_linked) return null; // not linked: UnitNotLinked explains it
  return <div className={`note ${zone.notice_level === 'warn' ? 'warn' : 'info'}`}>{zone.notice}</div>;
}

/** Neutral block for a zone without airco (never red). Shows the sensor values when there is one. */
export function UnitNotLinked({ zone, onLink }: { zone: Zone; onLink?: () => void }) {
  const { admin } = useClimateCan();
  const tm = zone.temperature;
  const hasSensor = Boolean(zone.sensor_device_id) || tm.value !== null;
  return (
    <div className="stack tight">
      <strong>Nog geen airco gekoppeld</strong>
      {hasSensor && (
        <div className="c-readings">
          <span className={`big-num${tm.stale || tm.value === null ? ' stale' : ''}`} style={{ fontSize: 40 }}>{temp(tm.value)}</span>
          <span className="small row" style={{ gap: 8 }}>
            <QualityBadge quality={tm.quality} />
            <span className="muted">{measurementLine(zone) || 'nog geen meting'}</span>
          </span>
        </div>
      )}
      <p className="small muted" style={{ margin: 0 }}>
        {hasSensor
          ? 'De sensor meet al. De regeling begint zodra er een airco aan deze zone gekoppeld is.'
          : 'Koppel een airco om deze zone te regelen. Tot die tijd gebeurt er niets.'}
      </p>
      {admin ? (
        <div className="c-actions">
          <SafeLink to="/installatie" className="btn primary">Airco koppelen</SafeLink>
          <SafeLink to="/apparaten" className="btn">Naar Apparaten</SafeLink>
          {onLink && <button type="button" className="btn" onClick={onLink}>Unit kiezen voor deze zone</button>}
        </div>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>Een beheerder kan een airco koppelen.</p>
      )}
    </div>
  );
}

export function ModeButtons({ zone, onPick, disabled }: { zone: Zone; onPick: (m: HvacMode) => void; disabled?: boolean }) {
  const blocked = new Map(zone.blocked_modes.map((b) => [b.mode, b.reason]));
  const modes: HvacMode[] = ['off', ...zone.capabilities.modes.filter((m) => m !== 'off')];
  const noteId = `blocked-${zone.id}`;
  return (
    <fieldset className="c-fieldset">
      <legend>Modus</legend>
      <div className="mode-grid">
        {modes.map((m) => {
          const reason = blocked.get(m);
          return (
            <button
              key={m}
              type="button"
              className={`mode-btn ${m}${reason ? ' blocked' : ''}`}
              aria-pressed={zone.mode === m}
              aria-describedby={reason ? noteId : undefined}
              disabled={disabled || Boolean(reason)}
              onClick={() => onPick(m)}
            >
              {MODE_LABEL[m] ?? m}
            </button>
          );
        })}
      </div>
      {zone.blocked_modes.length > 0 && (
        <div id={noteId} className="note info">
          {zone.blocked_modes.map((b) => (
            <div key={b.mode}><strong>{MODE_LABEL[b.mode]}</strong> niet mogelijk: {b.reason}</div>
          ))}
        </div>
      )}
    </fieldset>
  );
}

const FAN_CHOICES: FanSpeed[] = ['auto', 'low', 'medium', 'high'];

export function FanButtons({ zone, onPick, disabled, describedBy }: { zone: Zone; onPick: (f: FanSpeed) => void; disabled: boolean; describedBy?: string }) {
  return (
    <fieldset className="c-fieldset">
      <legend>Ventilator</legend>
      <div className="c-choices">
        {FAN_CHOICES.map((f) => (
          <button key={f} type="button" aria-pressed={zone.fan === f} disabled={disabled} aria-describedby={describedBy} onClick={() => onPick(f)}>
            {t(FAN_LABEL, f)}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function ResumeButton({ zone, small }: { zone: Zone; small?: boolean }) {
  const qc = useQueryClient();
  const { homeId } = useHome();
  const resume = useMutation({ mutationFn: () => api.zoneResume(zone.id), onSuccess: (z) => storeZone(qc, homeId, z) });
  if (!zone.can_resume) return null;
  return (
    <>
      <ActionButton className={`btn${small ? ' small' : ''}`} disabled={resume.isPending} onClick={() => resume.mutate()}>
        Terug naar schema
      </ActionButton>
      {resume.error ? <FormError error={resume.error} /> : null}
    </>
  );
}

/* ---------- Overzicht: compact card ---------- */

export function ZoneCard({ zone, tz }: { zone: Zone; tz?: string }) {
  const { control } = useClimateCan();
  const ctl = useZoneControl(zone);
  const tm = zone.temperature;
  const reason = disabledReason(zone);
  const hintId = `zc-hint-${zone.id}`;
  const shown = ctl.sp.value;
  const draft = shown !== zone.target_c;
  const bandLeft = 30;
  const markPos = zone.target_c !== null && tm.value !== null
    ? Math.max(2, Math.min(98, 50 + ((tm.value - zone.target_c) / Math.max(zone.policy.band_c * 2, 0.5)) * 20))
    : null;

  return (
    <article className={`card live${zone.unit_linked ? '' : ' c-unlinked'}`} aria-labelledby={`zone-${zone.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <div>
          <h3 id={`zone-${zone.id}`}>{zone.name}</h3>
          <span className="card-sub">
            {[zone.room !== zone.name ? zone.room : null, zone.profile ? `profiel ${zone.profile}` : null, zone.unit_linked ? controlText(zone, tz) : null]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
        <ModePill zone={zone} />
      </div>
      {!zone.unit_linked ? (
        <UnitNotLinked zone={zone} />
      ) : (
        <>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <span className={`big-num${tm.stale || tm.value === null ? ' stale' : ''}`} style={{ fontSize: 56 }}>{temp(tm.value)}</span>
              <span className="small" style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', color: tm.stale ? 'var(--warn)' : 'var(--muted)' }}>
                <QualityBadge quality={tm.quality} />
                {measurementLine(zone)}
              </span>
            </div>
            <div className="c-setpoint">
              {control && (
                <button type="button" className="sp-btn sm" aria-label={`Doeltemperatuur ${zone.name} lager`} aria-describedby={reason ? hintId : undefined} disabled={!ctl.adjustable} onClick={() => ctl.bump(-1)}>−</button>
              )}
              <div className="c-setpoint-val">
                <span className="small muted">Doel</span>
                <span style={{ fontSize: 22, fontWeight: 600 }} className={draft ? 'draft' : undefined} aria-live="polite">{temp(shown)}</span>
              </div>
              {control && (
                <button type="button" className="sp-btn sm" aria-label={`Doeltemperatuur ${zone.name} hoger`} aria-describedby={reason ? hintId : undefined} disabled={!ctl.adjustable} onClick={() => ctl.bump(1)}>+</button>
              )}
            </div>
          </div>
          {zone.mode === 'heat' && markPos !== null && (
            <div className="band" aria-hidden="true">
              <div className="band-fill" style={{ left: `${bandLeft}%`, width: '40%' }} />
              <div className="band-mark" style={{ left: `${markPos}%` }} />
            </div>
          )}
          <CommandLine ctl={ctl} />
          {control && reason && <span id={hintId} className="c-hint">{reason}</span>}
          <ZoneNotice zone={zone} />
          {zone.next_change && zone.control !== 'off' && (
            <span className="card-sub">
              Comfortband ±{zone.policy.band_c.toLocaleString('nl-NL')}° · volgende wijziging {time(zone.next_change.at, tz)} naar {zone.next_change.label || 'schemablok'} ({temp(zone.next_change.target_c)})
            </span>
          )}
          {control && zone.can_resume && <div className="c-actions"><ResumeButton zone={zone} small /></div>}
        </>
      )}
    </article>
  );
}

/* ---------- Klimaat: full control card ---------- */

export function ZoneControlPanel({ zone, tz, onLink }: { zone: Zone; tz?: string; onLink?: () => void }) {
  const { control } = useClimateCan();
  const ctl = useZoneControl(zone);
  const tm = zone.temperature;
  const reason = disabledReason(zone);
  const hintId = `zp-hint-${zone.id}`;
  const shown = ctl.sp.value;
  const draft = shown !== zone.target_c;

  return (
    <section aria-labelledby={`bed-h-${zone.id}`} className={`card live c-control${zone.unit_linked ? '' : ' c-unlinked'}`}>
      <div className="section-head">
        <h2 id={`bed-h-${zone.id}`} style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Bediening</h2>
        <span className="small muted">{controlText(zone, tz)}</span>
      </div>
      {!zone.unit_linked ? (
        <UnitNotLinked zone={zone} onLink={onLink} />
      ) : (
        <>
          <div className="c-dial-row">
            {control && (
              <button type="button" className="sp-btn" aria-label="Doeltemperatuur lager" aria-describedby={reason ? hintId : undefined} disabled={!ctl.adjustable} onClick={() => ctl.bump(-1)}>−</button>
            )}
            <div className={`c-dial ${zone.mode}`}>
              <span className="small muted">Doel</span>
              <span className={`big-num${draft ? ' draft' : ''}`} aria-live="polite">{temp(shown)}</span>
              <span className={`small ${tm.stale ? '' : 'muted'}`} style={tm.stale ? { color: 'var(--warn)' } : undefined}>nu {temp(tm.value)}</span>
            </div>
            {control && (
              <button type="button" className="sp-btn" aria-label="Doeltemperatuur hoger" aria-describedby={reason ? hintId : undefined} disabled={!ctl.adjustable} onClick={() => ctl.bump(1)}>+</button>
            )}
          </div>
          <span className="small row" style={{ justifyContent: 'center', gap: 8 }}>
            <QualityBadge quality={tm.quality} /> <span className="muted">{measurementLine(zone)}</span>
          </span>
          <CommandLine ctl={ctl} />
          {control && reason && <p id={hintId} className="c-hint" style={{ margin: 0 }}>{reason}</p>}
          <ZoneNotice zone={zone} />
          {control ? (
            <>
              <ModeButtons zone={zone} onPick={ctl.sendMode} disabled={!ctl.modesEnabled} />
              {zone.capabilities.fan && <FanButtons zone={zone} onPick={ctl.sendFan} disabled={!ctl.adjustable} describedBy={reason ? hintId : undefined} />}
              <div className="c-actions">
                <ActionButton className="btn primary" disabled={!ctl.adjustable} aria-describedby={reason ? hintId : undefined} onClick={ctl.sendOverride}>
                  Handmatig {durationText(ctl.overrideMinutes)}
                </ActionButton>
                <ResumeButton zone={zone} />
              </div>
            </>
          ) : (
            <dl className="facts">
              <div><dt>Modus</dt><dd>{MODE_LABEL[zone.mode] ?? zone.mode}</dd></div>
              {zone.capabilities.fan && <div><dt>Ventilator</dt><dd>{t(FAN_LABEL, zone.fan)}</dd></div>}
            </dl>
          )}
        </>
      )}
    </section>
  );
}
