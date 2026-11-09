// "Regelgrenzen" for one zone (PUT /zones/{id}/policy is partial: only changed fields are sent).
// Item 45: energy priority, watchdog and plausibility limits for sensor values are part of the form.
import './climate.css';
import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { EnergyPriority, Zone, ZonePolicyFull, ZonePolicyIn } from '../api/types';
import { ENERGY_PRIORITY_LABEL } from '../i18n';
import { ActionButton } from '../components/bits';
import { FormError, num, storeZone } from './common';

export const PRIORITY_HELP: Record<EnergyPriority, string> = {
  comfort: 'Smallere comfortband (×0,75): de temperatuur blijft dichter bij het doel, het kost iets meer energie.',
  balanced: 'De comfortband zoals hierboven ingesteld.',
  saving: 'Bredere comfortband (×1,5) en minder bijsturen in de dure richting: zuiniger, iets minder nauwkeurig.',
};

type NumKey = Exclude<keyof ZonePolicyFull, 'energy_priority'>;

interface FieldDef {
  key: NumKey;
  label: string;
  /** Shown value = API value / factor (seconds → minutes). */
  factor: number;
  min: number;
  max: number;
  step: number;
  integer: boolean;
  help?: string;
}

const FIELDS: FieldDef[] = [
  { key: 'band_c', label: 'Comfortband ± °C', factor: 1, min: 0.2, max: 3, step: 0.1, integer: false },
  { key: 'min_interval_s', label: 'Min. tijd tussen wijzigingen (min)', factor: 60, min: 5, max: 120, step: 1, integer: true },
  { key: 'max_step_c', label: 'Max. setpoint-sprong °C', factor: 1, min: 0.5, max: 3, step: 0.5, integer: false },
  { key: 'max_starts_per_hour', label: 'Max. starts per uur', factor: 1, min: 1, max: 6, step: 1, integer: true },
  { key: 'stale_after_s', label: 'Sensor verouderd na (min)', factor: 60, min: 5, max: 120, step: 1, integer: true },
  { key: 'max_offset_c', label: 'Max. bijsturing met de sensor ± °C', factor: 1, min: 0, max: 3, step: 0.1, integer: false, help: 'Hoeveel de regeling het doel van de airco mag verschuiven op basis van de externe sensor.' },
  { key: 'override_default_minutes', label: 'Duur van „Handmatig” (min)', factor: 1, min: 15, max: 1440, step: 15, integer: true },
];
const WATCHDOG: FieldDef[] = [
  { key: 'watchdog_minutes', label: 'Waakhond na (min, 0 = uit)', factor: 1, min: 0, max: 720, step: 5, integer: true, help: 'Een melding als de kamer in deze tijd niet richting het doel beweegt terwijl de airco draait.' },
  { key: 'watchdog_min_progress_c', label: 'Minimale vooruitgang °C', factor: 1, min: 0.1, max: 3, step: 0.1, integer: false },
];
const PLAUSIBLE: FieldDef[] = [
  { key: 'plausible_min_c', label: 'Ondergrens °C', factor: 1, min: -30, max: 30, step: 0.5, integer: false },
  { key: 'plausible_max_c', label: 'Bovengrens °C', factor: 1, min: 10, max: 60, step: 0.5, integer: false },
];
const ALL = [...FIELDS, ...WATCHDOG, ...PLAUSIBLE];

const show = (v: number, f: FieldDef) => String(Number((v / f.factor).toFixed(2))).replace('.', ',');

function initial(p: ZonePolicyFull): Record<NumKey, string> {
  return Object.fromEntries(ALL.map((f) => [f.key, show(p[f.key], f)])) as Record<NumKey, string>;
}

/** Validate and build the partial PUT body (only changed fields). Returns Dutch problems instead when invalid. */
export function policyChanges(p: ZonePolicyFull, form: Record<NumKey, string>, priority: EnergyPriority): { body: ZonePolicyIn; problems: string[] } {
  const problems: string[] = [];
  const body: ZonePolicyIn = {};
  const values: Partial<Record<NumKey, number>> = {};
  for (const f of ALL) {
    const raw = num(form[f.key]);
    if (!Number.isFinite(raw)) {
      problems.push(`${f.label}: vul een getal in.`);
      continue;
    }
    if (raw < f.min || raw > f.max) {
      problems.push(`${f.label}: kies een waarde van ${String(f.min).replace('.', ',')} tot ${String(f.max).replace('.', ',')}.`);
      continue;
    }
    const v = f.integer ? Math.round(raw * f.factor) : Number((raw * f.factor).toFixed(2));
    values[f.key] = v;
    if (v !== p[f.key]) (body as Record<string, unknown>)[f.key] = v;
  }
  const wd = values.watchdog_minutes;
  if (wd !== undefined && wd > 0 && wd < 15) problems.push('Waakhond: kies 0 (uit) of minstens 15 minuten.');
  const lo = values.plausible_min_c ?? p.plausible_min_c;
  const hi = values.plausible_max_c ?? p.plausible_max_c;
  if (lo >= hi) problems.push('De ondergrens voor sensorwaarden moet lager zijn dan de bovengrens.');
  if (priority !== p.energy_priority) body.energy_priority = priority;
  return { body, problems };
}

export function PolicyForm({ zone, homeId }: { zone: Zone; homeId: string }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(() => initial(zone.policy));
  const [priority, setPriority] = useState<EnergyPriority>(zone.policy.energy_priority);
  const [problems, setProblems] = useState<string[]>([]);
  const [info, setInfo] = useState<string | null>(null);
  const policyKey = JSON.stringify(zone.policy);
  useEffect(() => {
    setForm(initial(zone.policy));
    setPriority(zone.policy.energy_priority);
  }, [policyKey]); // follow saved/external changes

  const save = useMutation({
    mutationFn: (body: ZonePolicyIn) => api.putZonePolicy(zone.id, body),
    onSuccess: (z) => {
      storeZone(qc, homeId, z);
      qc.invalidateQueries({ queryKey: ['zonelog', z.id] });
      setInfo('Regelgrenzen opgeslagen.');
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setInfo(null);
    const { body, problems: p } = policyChanges(zone.policy, form, priority);
    setProblems(p);
    if (p.length) return;
    if (!Object.keys(body).length) {
      setInfo('Er is niets gewijzigd.');
      return;
    }
    save.mutate(body);
  };
  const field = (f: FieldDef) => {
    const helpId = `pol-${zone.id}-${f.key}-help`;
    const input = (
      <label className="field">
        {f.label}
        <input inputMode="decimal" required value={form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} aria-describedby={f.help ? helpId : undefined} />
      </label>
    );
    if (!f.help) return <div key={f.key}>{input}</div>;
    return (
      <div key={f.key} className="stack tight">
        {input}
        <span id={helpId} className="small muted">{f.help}</span>
      </div>
    );
  };
  const pid = `prio-${zone.id}`;
  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="form-grid">{FIELDS.map(field)}</div>
      <fieldset className="choice">
        <legend>Energieprioriteit</legend>
        {(['comfort', 'balanced', 'saving'] as EnergyPriority[]).map((k) => (
          <label key={k} className={`choice-card${priority === k ? ' selected' : ''}`}>
            <input type="radio" name={pid} value={k} checked={priority === k} onChange={() => setPriority(k)} />
            <span>
              <strong>{ENERGY_PRIORITY_LABEL[k]}</strong>
              <span className="small muted">{PRIORITY_HELP[k]}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <fieldset className="c-fieldset">
        <legend>Waakhond</legend>
        <div className="form-grid">{WATCHDOG.map(field)}</div>
      </fieldset>
      <fieldset className="c-fieldset">
        <legend>Geldige sensorwaarden (alles daarbuiten wordt genegeerd)</legend>
        <div className="form-grid">{PLAUSIBLE.map(field)}</div>
      </fieldset>
      {problems.length > 0 && (
        <div className="note error" role="alert">
          {problems.length === 1 ? problems[0] : <ul className="plain-list">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        </div>
      )}
      <div className="c-actions">
        <ActionButton type="submit" className="btn primary" disabled={save.isPending}>Regelgrenzen opslaan</ActionButton>
      </div>
      <span className="small muted" aria-live="polite">{info}</span>
      <FormError error={save.error} />
    </form>
  );
}
