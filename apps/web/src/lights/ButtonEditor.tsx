import { useId, useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, isApiError } from '../api/client';
import { qk } from '../api/keys';
import type { ButtonMapping, HueButton, Lights, MappingMode, SimulateResult, Zone } from '../api/types';
import { ActionButton, Empty, ErrorNote } from '../components/bits';
import { ago } from '../format';
import { FAN_LABEL, MAPPING_MODE_LABEL, MODE_LABEL, STEP_RESULT_LABEL, t } from '../i18n';
import {
  describeSimulated,
  describeStep,
  draftErrors,
  draftFromMapping,
  emptyStep,
  eventLabel,
  inputLabel,
  mappingBody,
  mappingRows,
  type MappingDraft,
  type MappingRowModel,
  type Names,
  type StepDraft,
} from './mapping';

const MODE_HELP: Record<MappingMode, string> = {
  lights: 'Alleen lampen, groepen en scènes.',
  climate: 'Alleen airco-zones (met dezelfde veiligheidscontroles als in de app).',
  combined: 'Licht én airco met één druk. Vraagt een bewuste bevestiging.',
};
const FANS = ['auto', 'low', 'medium_low', 'medium', 'medium_high', 'high', 'quiet', 'turbo'];

function StepEditor({ step, index, onChange, onRemove, lights, zones }: { step: StepDraft; index: number; onChange: (s: StepDraft) => void; onRemove: () => void; lights: Lights | null; zones: Zone[] }) {
  const id = useId();
  const set = (patch: Partial<StepDraft>) => onChange({ ...step, ...patch });
  const groups = lights?.groups ?? [];
  const scenes = lights?.scenes ?? [];
  const lamps = lights?.lights ?? [];
  const isScene = scenes.some((s) => s.id === step.device_id);
  const zone = zones.find((z) => z.id === step.zone_id);
  const modes = zone ? [...new Set(['off', ...zone.capabilities.modes])] : ['off', 'heat', 'cool', 'auto', 'dry', 'fan'];
  const title = `Stap ${index + 1} · ${step.type === 'light' ? 'Licht' : 'Klimaat'}`;
  return (
    <li className="lt-step" aria-label={title} data-testid={`step-${index + 1}`}>
      <div className="lt-step-head">
        <strong>{title}</strong>
        <button type="button" className="btn small ghost" aria-label={`Stap ${index + 1} verwijderen`} onClick={onRemove}>Verwijderen</button>
      </div>
      {step.type === 'light' ? (
        <div className="form-grid">
          <label className="field" htmlFor={`${id}-t`}>
            Lamp, groep of scène
            <select id={`${id}-t`} value={step.device_id} onChange={(e) => set({ device_id: e.target.value })}>
              <option value="">Kies…</option>
              {groups.length > 0 && <optgroup label="Kamers en groepen">{groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</optgroup>}
              {scenes.length > 0 && (
                <optgroup label="Scènes">
                  {scenes.map((s) => <option key={s.id} value={s.id}>{s.name}{s.group_id ? ` (${groups.find((g) => g.id === s.group_id)?.name ?? 'groep'})` : ''}</option>)}
                </optgroup>
              )}
              {lamps.length > 0 && <optgroup label="Lampen">{lamps.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</optgroup>}
            </select>
          </label>
          {!isScene && (
            <label className="field" htmlFor={`${id}-a`}>
              Actie
              <select id={`${id}-a`} value={step.lightAction} onChange={(e) => set({ lightAction: e.target.value as StepDraft['lightAction'] })}>
                <option value="set">Aan/uit en helderheid instellen</option>
                <option value="toggle">Aan/uit wisselen</option>
              </select>
            </label>
          )}
          {!isScene && step.lightAction === 'set' && (
            <>
              <label className="field" htmlFor={`${id}-o`}>
                Aan of uit
                <select id={`${id}-o`} value={step.on} onChange={(e) => set({ on: e.target.value as StepDraft['on'] })}>
                  <option value="on">Aan</option>
                  <option value="off">Uit</option>
                  <option value="">Niet wijzigen</option>
                </select>
              </label>
              {step.on !== 'off' && (
                <label className="field" htmlFor={`${id}-b`}>
                  Helderheid (1–100 %)
                  <input id={`${id}-b`} inputMode="numeric" placeholder="niet wijzigen" value={step.brightness} onChange={(e) => set({ brightness: e.target.value })} />
                </label>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="form-grid">
          <label className="field" htmlFor={`${id}-z`}>
            Klimaatzone
            <select id={`${id}-z`} value={step.zone_id} onChange={(e) => set({ zone_id: e.target.value })}>
              <option value="">Kies…</option>
              {zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
            </select>
          </label>
          <label className="field" htmlFor={`${id}-za`}>
            Actie
            <select id={`${id}-za`} value={step.zoneAction} onChange={(e) => set({ zoneAction: e.target.value as StepDraft['zoneAction'] })}>
              <option value="set">Instellen</option>
              <option value="resume">Terug naar schema</option>
            </select>
          </label>
          {step.zoneAction === 'set' && (
            <>
              <label className="field" htmlFor={`${id}-m`}>
                Modus
                <select id={`${id}-m`} value={step.mode} onChange={(e) => set({ mode: e.target.value })}>
                  <option value="">Niet wijzigen</option>
                  {modes.map((m) => <option key={m} value={m}>{t(MODE_LABEL, m)}</option>)}
                </select>
              </label>
              <label className="field" htmlFor={`${id}-tc`}>
                Doeltemperatuur (°C)
                <input id={`${id}-tc`} inputMode="decimal" placeholder="niet wijzigen" value={step.target_c} onChange={(e) => set({ target_c: e.target.value })} />
              </label>
              {(!zone || zone.capabilities.fan) && (
                <label className="field" htmlFor={`${id}-f`}>
                  Ventilator
                  <select id={`${id}-f`} value={step.fan} onChange={(e) => set({ fan: e.target.value })}>
                    <option value="">Niet wijzigen</option>
                    {FANS.map((f) => <option key={f} value={f}>{t(FAN_LABEL, f)}</option>)}
                  </select>
                </label>
              )}
              <label className="field" htmlFor={`${id}-d`}>
                Hoe lang (minuten)
                <input id={`${id}-d`} inputMode="numeric" placeholder={zone ? `standaard ${zone.policy.override_default_minutes}` : 'standaard'} value={step.override_minutes} onChange={(e) => set({ override_minutes: e.target.value })} />
              </label>
            </>
          )}
        </div>
      )}
    </li>
  );
}

function MappingEditor({ hb, row, mapping, names, lights, zones, homeId, onDone }: { hb: HueButton; row: MappingRowModel; mapping: ButtonMapping | null; names: Names; lights: Lights | null; zones: Zone[]; homeId: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<MappingDraft>(() => draftFromMapping(mapping));
  const [tried, setTried] = useState(false);
  const errs = draftErrors(draft, names);
  const title = `${row.inputLabel} · ${eventLabel(row.event)}`;
  const save = useMutation({
    mutationFn: async () => {
      const body = mappingBody(draft, row.button, row.event, names);
      if (mapping) {
        const { mode, action, combined_confirmed, enabled } = body;
        return api.patchButtonMapping(mapping.id, { mode, action, combined_confirmed, enabled });
      }
      return api.addButtonMapping(hb.id, body);
    },
    onSuccess: (updated) => {
      if (updated && typeof updated === 'object' && 'mapping' in updated) qc.setQueryData<HueButton[]>(qk.buttons(homeId), (l) => l?.map((x) => (x.id === updated.id ? updated : x)));
      qc.invalidateQueries({ queryKey: qk.buttons(homeId) });
      onDone();
    },
  });
  const setMode = (mode: MappingMode) => {
    let steps = draft.steps;
    // Add the step kind a mode needs when the draft has none yet (nothing is removed silently).
    if (mode !== 'climate' && !steps.some((s) => s.type === 'light')) steps = [...steps, emptyStep('light')];
    if (mode !== 'lights' && !steps.some((s) => s.type === 'zone')) steps = [...steps, emptyStep('zone')];
    if (mode === 'climate') steps = steps.filter((s) => s.type === 'zone' || s.device_id);
    if (mode === 'lights') steps = steps.filter((s) => s.type === 'light' || s.zone_id);
    setDraft({ ...draft, mode, steps });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (errs.length) return;
    save.mutate();
  };
  const conflicts = isApiError(save.error, 'conflict') ? (save.error.detail.conflicts ?? []) : [];
  const fid = useId();
  return (
    <form className="lt-editor stack" onSubmit={submit} aria-label={`Koppeling ${title}`} noValidate>
      <fieldset className="choice">
        <legend>Wat bedient deze knop?</legend>
        {(['lights', 'climate', 'combined'] as MappingMode[]).map((m) => (
          <label key={m} className={`choice-card${draft.mode === m ? ' selected' : ''}`}>
            <input type="radio" name={`${fid}-mode`} value={m} checked={draft.mode === m} onChange={() => setMode(m)} />
            <span><strong>{MAPPING_MODE_LABEL[m]}</strong><span className="small muted">{MODE_HELP[m]}</span></span>
          </label>
        ))}
      </fieldset>
      <ol className="plain-list lt-steps">
        {draft.steps.map((s, i) => (
          <StepEditor
            key={s.key}
            step={s}
            index={i}
            lights={lights}
            zones={zones}
            onChange={(n) => setDraft({ ...draft, steps: draft.steps.map((x) => (x.key === s.key ? n : x)) })}
            onRemove={() => setDraft({ ...draft, steps: draft.steps.filter((x) => x.key !== s.key) })}
          />
        ))}
      </ol>
      <div className="row">
        {draft.mode !== 'climate' && <button type="button" className="btn small" onClick={() => setDraft({ ...draft, steps: [...draft.steps, emptyStep('light')] })}>Lichtstap toevoegen</button>}
        {draft.mode !== 'lights' && <button type="button" className="btn small" onClick={() => setDraft({ ...draft, steps: [...draft.steps, emptyStep('zone')] })}>Klimaatstap toevoegen</button>}
      </div>
      {draft.mode === 'combined' && (
        <label className="check-row lt-confirm">
          <input type="checkbox" checked={draft.combined_confirmed} onChange={(e) => setDraft({ ...draft, combined_confirmed: e.target.checked })} />
          Ik wil bewust dat één druk op deze knop zowel het licht als de airco bedient.
        </label>
      )}
      <label className="check-row">
        <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
        Koppeling actief
      </label>
      {row.bridgeRule && draft.mode !== 'climate' && (
        <p className="note warn" style={{ margin: 0 }}>
          De Hue Bridge voert voor deze knop zelf al een actie uit{hb.bridge_rule_names.length ? ` (${hb.bridge_rule_names.join(', ')})` : ''}. Een lichtactie hier kan dubbel schakelen. Haal de regel weg in de Hue-app, of koppel hier alleen klimaat.
        </p>
      )}
      {tried && errs.length > 0 && (
        <div className="note error" role="alert">
          Controleer het volgende:
          <ul className="plain-list">{errs.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      {save.error && !conflicts.length && <ErrorNote error={save.error} />}
      {conflicts.length > 0 && (
        <div className="note error" role="alert">
          Niet opgeslagen, er is een conflict:
          <ul className="plain-list">{conflicts.map((c) => <li key={`${c.index}-${c.message}`}>{c.message}</li>)}</ul>
        </div>
      )}
      <div className="row">
        <ActionButton type="submit" className="btn primary" disabled={save.isPending}>{save.isPending ? 'Opslaan…' : 'Koppeling opslaan'}</ActionButton>
        <button type="button" className="btn" onClick={onDone}>Annuleren</button>
      </div>
    </form>
  );
}

export function SimulationView({ result, names, onClose }: { result: SimulateResult; names: Names; onClose: () => void }) {
  return (
    <div className="note info lt-sim" data-testid="simulation">
      <strong>Simulatie (er is niets uitgevoerd):</strong> {result.message}
      {result.mappings.map((m) => (
        <div key={m.mapping_id} className="lt-sim-map">
          <span className="small"><strong>{MAPPING_MODE_LABEL[m.mode]}</strong></span>
          {m.skipped && <p className="small" style={{ margin: 0 }}>{m.skipped}</p>}
          {m.conflict && <p className="small" style={{ margin: 0 }}>Conflict: {m.conflict}</p>}
          {m.steps.length > 0 && (
            <ul className="plain-list lt-sim-steps">
              {m.steps.map((s, i) => (
                <li key={i}>
                  <span className={`pill ${s.result === 'would_run' ? 'ok' : s.result === 'refused' ? 'warn' : 'error'}`}>{t(STEP_RESULT_LABEL, s.result)}</span>{' '}
                  {describeSimulated(s, names)}
                  {s.reason ? <span> — {s.reason}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      <div><button type="button" className="btn small" onClick={onClose}>Sluiten</button></div>
    </div>
  );
}

function MappingRow({ hb, row, names, lights, zones, homeId, admin }: { hb: HueButton; row: MappingRowModel; names: Names; lights: Lights | null; zones: Zone[]; homeId: string; admin: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<ButtonMapping | 'new' | null>(null);
  const [sim, setSim] = useState<SimulateResult | null>(null);
  const title = `${row.inputLabel} · ${eventLabel(row.event)}`;
  const simulate = useMutation({ mutationFn: () => api.simulateButton(hb.id, row.button, row.event), onSuccess: setSim });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteButtonMapping(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.buttons(homeId) }),
  });
  return (
    <li className="lt-map-row" aria-label={title} data-testid={`row-${row.button}-${row.event}`}>
      <div className="lt-map-head">
        <strong>{eventLabel(row.event)}</strong>
        {row.mappings.length === 0 && (
          <span className="small muted">{row.bridgeRule ? 'Niets in de app; de Hue Bridge regelt deze knop zelf.' : 'Niets gekoppeld.'}</span>
        )}
      </div>
      {row.mappings.map((m) => (
        <div key={m.id} className={`lt-map${m.enabled ? '' : ' is-off'}`}>
          <div className="row" style={{ gap: 6 }}>
            <span className="pill neutral">{MAPPING_MODE_LABEL[m.mode]}</span>
            {!m.enabled && <span className="pill neutral">Staat uit</span>}
            {m.mode === 'combined' && !m.combined_confirmed && <span className="pill warn">Niet bevestigd</span>}
          </div>
          <ul className="plain-list small lt-map-steps">
            {(m.action?.steps ?? []).map((s, i) => <li key={i}>{describeStep(s, names)}</li>)}
          </ul>
          {m.conflict && <p className="note warn lt-conflict" style={{ margin: 0 }}><strong>Conflict:</strong> {m.conflict}</p>}
          {admin && editing === null && (
            <div className="row" style={{ gap: 6 }}>
              <button type="button" className="btn small" aria-label={`Koppeling ${title} bewerken`} onClick={() => setEditing(m)}>Bewerken</button>
              <ActionButton
                className="btn small danger"
                aria-label={`Koppeling ${title} verwijderen`}
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm(`Koppeling ‘${title}’ verwijderen? De knop doet daarna in de app niets meer bij deze gebeurtenis.`)) remove.mutate(m.id);
                }}
              >
                Verwijderen
              </ActionButton>
            </div>
          )}
        </div>
      ))}
      {admin && editing === null && (
        <div className="row" style={{ gap: 6 }}>
          {row.mappings.length === 0 && <button type="button" className="btn small" aria-label={`Koppeling maken voor ${title}`} onClick={() => setEditing('new')}>Koppeling maken</button>}
          <ActionButton className="btn small" aria-label={`Simuleer ${title}`} disabled={simulate.isPending} onClick={() => simulate.mutate()}>{simulate.isPending ? 'Simuleren…' : 'Simuleer'}</ActionButton>
        </div>
      )}
      {editing !== null && (
        <MappingEditor hb={hb} row={row} mapping={editing === 'new' ? null : editing} names={names} lights={lights} zones={zones} homeId={homeId} onDone={() => setEditing(null)} />
      )}
      {remove.error && <ErrorNote error={remove.error} />}
      {simulate.error && <ErrorNote error={simulate.error} />}
      <div aria-live="polite">
        {sim && <SimulationView result={sim} names={names} onClose={() => setSim(null)} />}
      </div>
    </li>
  );
}

/** One Hue switch: its buttons × events, what each does in the app, conflicts, editor and simulation. */
export function ButtonCard({ hb, names, lights, zones, homeId, admin }: { hb: HueButton; names: Names; lights: Lights | null; zones: Zone[]; homeId: string; admin: boolean }) {
  const rows = mappingRows(hb);
  const byInput = new Map<string, MappingRowModel[]>();
  for (const r of rows) byInput.set(`${r.inputLabel}|${r.button}`, [...(byInput.get(`${r.inputLabel}|${r.button}`) ?? []), r]);
  const inputIndex = new Map(hb.buttons.map((b, i) => [b.id, inputLabel(b, i)]));
  const facts = [hb.model, hb.online ? 'bereikbaar' : 'niet bereikbaar', hb.battery !== null ? `batterij ${hb.battery}%` : null].filter(Boolean).join(' · ');
  return (
    <article className="card lt-button" aria-labelledby={`b-${hb.id}`}>
      <div className="section-head">
        <h3 id={`b-${hb.id}`}>{hb.name}</h3>
        <span className="small muted">{facts}</span>
      </div>
      {hb.last_event && (
        <p className="small muted" style={{ margin: 0 }}>
          Laatst ingedrukt: {inputIndex.get(hb.last_event.button) ?? 'knop'} · {eventLabel(hb.last_event.event)} · {ago(hb.last_event.at)}
        </p>
      )}
      {hb.bridge_rule_names.length > 0 && (
        <p className="note info" style={{ margin: 0 }}>
          Regels op de Hue Bridge zelf: {hb.bridge_rule_names.join(', ')}. Die blijven werken, ook als deze app uit staat.
        </p>
      )}
      {hb.buttons.length === 0 && (
        <p className="small muted" style={{ margin: 0 }}>De knoppen van dit apparaat zijn nog niet ingelezen. Druk één keer op elke knop; daarna verschijnen ze hier.</p>
      )}
      {rows.length === 0 ? (
        <Empty>Nog geen knoppen bekend.</Empty>
      ) : (
        [...byInput.entries()].map(([k, list]) => (
          <section key={k} className="lt-input" aria-label={list[0].inputLabel}>
            <div className="row" style={{ gap: 8 }}>
              <h4>{list[0].inputLabel}</h4>
              {list[0].bridgeRule && <span className="pill info">Ook een regel op de Hue Bridge</span>}
            </div>
            <ul className="plain-list lt-map-list">
              {list.map((r) => <MappingRow key={r.key} hb={hb} row={r} names={names} lights={lights} zones={zones} homeId={homeId} admin={admin} />)}
            </ul>
          </section>
        ))
      )}
    </article>
  );
}
