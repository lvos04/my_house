import { useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../api/client';
import type { GroupCommand, LightGroup, Scene } from '../api/types';
import { applyCommandSnapshot, runCommand, useLatestCommand } from '../commands';
import { ActionButton, ErrorNote } from '../components/bits';
import { BrightnessSlider } from './BrightnessSlider';
import { BulbIcon, BulbOffIcon, UnknownIcon } from '../components/Icons';

export function groupSummary(g: LightGroup): string {
  if (g.lights_total > 0) return `${g.lights_on} van ${g.lights_total} aan`;
  return g.on ? 'Aan' : 'Uit';
}

export function activeSceneName(g: LightGroup, scenes: Scene[]): string | null {
  return scenes.find((s) => s.active)?.name ?? g.scene ?? null;
}

/** A Hue room/zone: x of y on, active scene, unreachable lamps, on/off, brightness (sent on release only),
 * scenes. Commands are followed until the bridge confirms (command.updated); failures show the reason. */
export function GroupCard({ group, scenes, control }: { group: LightGroup; scenes: Scene[]; control: boolean }) {
  const key = `group:${group.id}`;
  const cmd = useLatestCommand(key);
  const last = group.last_command;
  // A failure the server reports (e.g. "Hue Bridge meldt: device has communication issues") is shown
  // even when this tab did not send the command. Older "sent" commands are not replayed: they would
  // only time out locally.
  useEffect(() => {
    if (last && (last.status === 'failed' || last.status === 'denied')) applyCommandSnapshot(key, last);
  }, [key, last]);
  const send = useMutation({
    mutationFn: ({ body, label }: { body: GroupCommand; label: string }) => runCommand(api.setGroup(group.id, body), { key, label }),
  });
  const active = activeSceneName(group, scenes);
  const unreachableLamps = group.lights_reachable !== null && group.lights_total > 0 ? group.lights_total - group.lights_reachable : 0;
  const disabled = !group.reachable || send.isPending;
  const failed = cmd && (cmd.phase === 'failed' || cmd.phase === 'denied' || cmd.phase === 'timeout' || cmd.phase === 'cancelled');
  const busy = send.isPending || cmd?.busy;
  const titleId = `g-${group.id}`;

  return (
    <article className={`card live lt-group${group.on ? ' is-on' : ''}`} aria-labelledby={titleId} data-testid={`group-${group.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <div className="stack tight" style={{ gap: 2 }}>
          <h3 id={titleId}>{group.name}</h3>
          <span className="card-sub">
            {groupSummary(group)}
            {active && group.on ? ` · scène ${active}` : ''}
          </span>
        </div>
        {control ? (
          // Zichtbaar "Aan"/"Uit" staat vooraan in de toegankelijke naam (WCAG 2.5.3), daarna de handeling.
          <ActionButton
            className={`btn${group.on ? ' primary' : ''}`}
            aria-pressed={group.on}
            aria-label={`${group.on ? 'Aan' : 'Uit'} — ${group.name} ${group.on ? 'uitzetten' : 'aanzetten'}`}
            disabled={disabled}
            onClick={() => send.mutate({ body: { on: !group.on }, label: group.on ? 'Uitzetten' : 'Aanzetten' })}
          >
            {group.on ? <BulbIcon /> : <BulbOffIcon />}
            {group.on ? 'Aan' : 'Uit'}
          </ActionButton>
        ) : (
          <span className={`pill ${group.on ? 'light' : 'neutral'}`}>{group.on ? <BulbIcon /> : <BulbOffIcon />}{group.on ? 'Aan' : 'Uit'}</span>
        )}
      </div>
      {!group.reachable && <span className="pill unknown lt-unreach"><UnknownIcon /><span>Onbekend — </span>niet bereikbaar</span>}
      {group.reachable && unreachableLamps > 0 && (
        <span className="small lt-warn">{unreachableLamps === 1 ? '1 lamp' : `${unreachableLamps} lampen`} niet bereikbaar (geen stroom of buiten bereik)</span>
      )}
      {control ? (
        <BrightnessSlider
          label={`Helderheid ${group.name}`}
          value={group.brightness}
          disabled={disabled}
          onCommit={(v) => send.mutate({ body: group.on ? { brightness: v } : { on: true, brightness: v }, label: `Helderheid ${v}%` })}
        />
      ) : (
        group.on && group.brightness !== null && <span className="small muted">Helderheid {group.brightness}%</span>
      )}
      {control && scenes.length > 0 && (
        <div className="lt-scenes" role="group" aria-label={`Scènes ${group.name}`}>
          {scenes.map((s) => (
            <ActionButton key={s.id} className={`btn small${s.active ? ' lt-scene-active' : ''}`} aria-pressed={s.active} disabled={disabled} onClick={() => send.mutate({ body: { scene_id: s.id }, label: `Scène ${s.name}` })}>
              {s.name}
            </ActionButton>
          ))}
        </div>
      )}
      {busy && !failed && (
        <span className="small muted" role="status">{cmd?.label ? `${cmd.label}: ` : ''}{send.isPending ? 'Wordt verzonden…' : cmd?.text}</span>
      )}
      {failed && cmd && (
        <div className="note error" role="alert">
          {cmd.label ? `${cmd.label}: ` : 'Laatste opdracht: '}
          {cmd.text}
        </div>
      )}
      {send.error && <ErrorNote error={send.error} />}
    </article>
  );
}
