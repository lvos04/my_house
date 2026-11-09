// Button mapping editor model (verbeterlijst 25): drafts for the form, the API body, client-side checks
// that mirror routers/lights.py (normalize_mapping), and Dutch descriptions of steps.
import type { ButtonInput, ButtonMapping, ButtonMappingIn, HueButton, Lights, MappingMode, MappingStep, SimulatedStep, Zone } from '../api/types';
import { BUTTON_EVENT_LABEL, BUTTON_TYPE_LABEL, FAN_LABEL, LIGHT_ACTION_LABEL, MODE_LABEL, t } from '../i18n';

export type TargetKind = 'group' | 'lamp' | 'scene';
export interface Target {
  id: string;
  name: string;
  kind: TargetKind;
}
export interface Names {
  targets: Map<string, Target>;
  zones: Map<string, string>;
}

export function buildNames(lights: Lights | null | undefined, zones: Zone[] | null | undefined): Names {
  const targets = new Map<string, Target>();
  const groupName = new Map<string, string>();
  for (const g of lights?.groups ?? []) {
    targets.set(g.id, { id: g.id, name: g.name, kind: 'group' });
    groupName.set(g.id, g.name);
  }
  for (const s of lights?.scenes ?? []) {
    const gn = s.group_id ? groupName.get(s.group_id) : undefined;
    targets.set(s.id, { id: s.id, name: gn ? `${s.name} (${gn})` : s.name, kind: 'scene' });
  }
  for (const l of lights?.lights ?? []) targets.set(l.id, { id: l.id, name: l.name, kind: 'lamp' });
  return { targets, zones: new Map((zones ?? []).map((z) => [z.id, z.name])) };
}

/* ---------- drafts ---------- */

export type LightAction = 'set_group' | 'set_light' | 'recall_scene' | 'toggle';
export interface StepDraft {
  key: number;
  type: 'light' | 'zone';
  device_id: string;
  /** "set" = set_group/set_light (chosen by the target), "toggle", or "recall_scene" for scenes. */
  lightAction: 'set' | 'toggle';
  on: '' | 'on' | 'off';
  brightness: string;
  zone_id: string;
  zoneAction: 'set' | 'resume';
  mode: string;
  target_c: string;
  fan: string;
  override_minutes: string;
}
export interface MappingDraft {
  mode: MappingMode;
  steps: StepDraft[];
  combined_confirmed: boolean;
  enabled: boolean;
}

let nextKey = 1;
export function emptyStep(type: 'light' | 'zone'): StepDraft {
  return { key: nextKey++, type, device_id: '', lightAction: 'set', on: 'on', brightness: '', zone_id: '', zoneAction: 'set', mode: '', target_c: '', fan: '', override_minutes: '' };
}

const num = (v: number | undefined | null) => (v === undefined || v === null ? '' : String(v).replace('.', ','));

export function draftFromMapping(m: ButtonMapping | null): MappingDraft {
  if (!m) return { mode: 'lights', steps: [emptyStep('light')], combined_confirmed: false, enabled: true };
  const steps = (m.action?.steps ?? []).map((s): StepDraft => {
    const d = emptyStep(s.type === 'zone' ? 'zone' : 'light');
    if (s.type === 'light') {
      d.device_id = s.device_id;
      d.lightAction = s.action === 'toggle' ? 'toggle' : 'set';
      d.on = s.params?.on === true ? 'on' : s.params?.on === false ? 'off' : '';
      d.brightness = num(s.params?.brightness);
    } else {
      d.zone_id = s.zone_id;
      if ('action' in s && s.action === 'resume') d.zoneAction = 'resume';
      else if (!('action' in s)) {
        d.mode = s.mode ?? '';
        d.target_c = num(s.target_c);
        d.fan = s.fan ?? '';
        d.override_minutes = num(s.override_minutes);
      }
    }
    return d;
  });
  return { mode: m.mode, steps: steps.length ? steps : [emptyStep(m.mode === 'climate' ? 'zone' : 'light')], combined_confirmed: m.combined_confirmed, enabled: m.enabled };
}

function parseNum(s: string): number | null {
  const v = s.trim().replace(',', '.');
  if (!v) return null;
  return /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : Number.NaN;
}

function lightActionFor(d: StepDraft, names: Names): LightAction {
  const kind = names.targets.get(d.device_id)?.kind;
  if (kind === 'scene') return 'recall_scene';
  if (d.lightAction === 'toggle') return 'toggle';
  return kind === 'lamp' ? 'set_light' : 'set_group';
}

export function stepFromDraft(d: StepDraft, names: Names): MappingStep {
  if (d.type === 'light') {
    const action = lightActionFor(d, names);
    const step: MappingStep = { type: 'light', device_id: d.device_id, action };
    if (action === 'set_group' || action === 'set_light') {
      const params: { on?: boolean; brightness?: number } = {};
      if (d.on) params.on = d.on === 'on';
      const b = parseNum(d.brightness);
      if (b !== null && d.on !== 'off') params.brightness = b;
      step.params = params;
    }
    return step;
  }
  if (d.zoneAction === 'resume') return { type: 'zone', zone_id: d.zone_id, action: 'resume' };
  const step: Record<string, unknown> = { type: 'zone', zone_id: d.zone_id };
  if (d.mode) step.mode = d.mode;
  const tc = parseNum(d.target_c);
  if (tc !== null) step.target_c = tc;
  if (d.fan) step.fan = d.fan;
  const om = parseNum(d.override_minutes);
  if (om !== null) step.override_minutes = om;
  return step as unknown as MappingStep;
}

/** The request body for POST /buttons/{id}/mappings (and the fields of PATCH /button-mappings/{id}). */
export function mappingBody(draft: MappingDraft, button: string, event: string, names: Names): ButtonMappingIn {
  return {
    button,
    event,
    mode: draft.mode,
    action: { steps: draft.steps.map((s) => stepFromDraft(s, names)) },
    combined_confirmed: draft.mode === 'combined' && draft.combined_confirmed,
    enabled: draft.enabled,
  };
}

/** Dutch problems, numbered per step like the server's messages. Empty = can be saved. */
export function draftErrors(draft: MappingDraft, names: Names): string[] {
  const errs: string[] = [];
  if (!draft.steps.length) errs.push('Voeg minstens één stap toe.');
  draft.steps.forEach((d, i) => {
    const where = `Stap ${i + 1}: `;
    if (d.type === 'light') {
      if (draft.mode === 'climate') return void errs.push(where + 'een klimaatkoppeling mag alleen airco-stappen bevatten.');
      if (!d.device_id) return void errs.push(where + 'kies een lamp, groep of scène.');
      const action = lightActionFor(d, names);
      if (action === 'set_group' || action === 'set_light') {
        const b = parseNum(d.brightness);
        if (b !== null && (Number.isNaN(b) || !Number.isInteger(b) || b < 1 || b > 100)) return void errs.push(where + 'helderheid moet een heel getal tussen 1 en 100 zijn.');
        if (!d.on && b === null) return void errs.push(where + 'kies aan/uit of een helderheid.');
      }
    } else {
      if (draft.mode === 'lights') return void errs.push(where + 'een lichtkoppeling mag alleen lamp-, groep- en scènestappen bevatten.');
      if (!d.zone_id) return void errs.push(where + 'kies een klimaatzone.');
      if (d.zoneAction === 'resume') return;
      const tc = parseNum(d.target_c);
      if (tc !== null && (Number.isNaN(tc) || tc < 5 || tc > 35)) return void errs.push(where + 'doeltemperatuur moet tussen 5 en 35 °C liggen.');
      const om = parseNum(d.override_minutes);
      if (om !== null && (Number.isNaN(om) || !Number.isInteger(om) || om < 5 || om > 1440)) return void errs.push(where + 'duur moet tussen 5 en 1440 minuten liggen.');
      if (!d.mode && tc === null && !d.fan) errs.push(where + 'kies een modus, temperatuur of ventilatorstand (of ‘terug naar schema’).');
    }
  });
  if (draft.mode === 'combined') {
    const types = new Set(draft.steps.map((s) => s.type));
    if (!(types.has('light') && types.has('zone'))) errs.push('Een gecombineerde koppeling heeft zowel een licht- als een airco-stap.');
    if (!draft.combined_confirmed) errs.push('Bevestig dat deze knop bewust zowel licht als airco bedient.');
  }
  return errs;
}

/* ---------- descriptions ---------- */

function lightParams(p: { on?: unknown; brightness?: unknown } | undefined): string {
  const parts: string[] = [];
  if (p?.on === true) parts.push('aan');
  if (p?.on === false) parts.push('uit');
  if (typeof p?.brightness === 'number') parts.push(`${p.brightness}%`);
  return parts.join(', ');
}

function zoneParams(s: { mode?: unknown; target_c?: unknown; fan?: unknown; override_minutes?: unknown }): string {
  const parts: string[] = [];
  if (typeof s.mode === 'string') parts.push(t(MODE_LABEL, s.mode));
  if (typeof s.target_c === 'number') parts.push(`${s.target_c.toLocaleString('nl-NL')}°`);
  if (typeof s.fan === 'string') parts.push(`ventilator ${t(FAN_LABEL, s.fan).toLowerCase()}`);
  if (typeof s.override_minutes === 'number') parts.push(`${s.override_minutes} min`);
  return parts.join(', ');
}

/** "Keuken: aan, 80%", "Woonkamer: Verwarmen, 21°", "Slaapkamer: terug naar schema". */
export function describeStep(s: MappingStep, names: Names): string {
  if (s.type === 'light') {
    const target = names.targets.get(s.device_id);
    const who = target?.name ?? 'Onbekende lamp of groep';
    if (s.action === 'recall_scene') return `Scène ${who}`;
    if (s.action === 'toggle') return `${who}: aan/uit wisselen`;
    return `${who}: ${lightParams(s.params) || t(LIGHT_ACTION_LABEL, s.action)}`;
  }
  const zone = names.zones.get(s.zone_id) ?? 'Onbekende zone';
  if ('action' in s && s.action === 'resume') return `${zone}: terug naar schema`;
  return `${zone}: ${zoneParams(s as Record<string, unknown>) || 'instellen'}`;
}

/** A simulated step in Dutch (server names included). */
export function describeSimulated(s: SimulatedStep, names: Names): string {
  if (s.type === 'zone') {
    const zone = s.zone_name ?? (s.zone_id ? names.zones.get(s.zone_id) : undefined) ?? 'Klimaatzone';
    if (s.action === 'resume') return `${zone}: terug naar schema`;
    return `${zone}: ${zoneParams(s.params ?? {}) || 'instellen'}`;
  }
  const who = s.device_name ?? (s.device_id ? names.targets.get(s.device_id)?.name : undefined) ?? 'Lamp of groep';
  if (s.action === 'recall_scene') return `Scène ${who}`;
  return `${who}: ${lightParams(s.params) || t(LIGHT_ACTION_LABEL, s.action ?? '')}`;
}

/* ---------- inputs and rows ---------- */

export function inputLabel(b: ButtonInput, index: number): string {
  const base = b.type === 'relative_rotary' ? t(BUTTON_TYPE_LABEL, b.type) : 'Knop';
  if (b.type === 'relative_rotary') return b.control_id ? `${base} ${b.control_id}` : base;
  return `${base} ${b.control_id ?? index + 1}`;
}

export function eventLabel(e: string): string {
  return t(BUTTON_EVENT_LABEL, e);
}

export interface MappingRowModel {
  key: string;
  button: string;
  event: string;
  inputLabel: string;
  bridgeRule: boolean;
  mappings: ButtonMapping[];
}

/** One row per physical button × event; mappings that fit no known button/event go last. When the
 * bridge inventory did not list the buttons yet, rows come from the mappings and the last press. */
export function mappingRows(hb: HueButton): MappingRowModel[] {
  const rows: MappingRowModel[] = [];
  const seen = new Set<string>();
  let inputs: ButtonInput[] = hb.buttons ?? [];
  if (!inputs.length) {
    const ids = [...new Set([...hb.mapping.map((m) => m.button), ...(hb.last_event ? [hb.last_event.button] : [])])];
    inputs = ids.map((id, i) => ({ id, control_id: i + 1, type: 'button', events: [], bridge_rule: hb.bridge_rules.includes(id) }));
  }
  inputs.forEach((b, i) => {
    const events = b.events?.length ? b.events : hb.events;
    for (const e of events) {
      const key = `${b.id}|${e}`;
      seen.add(key);
      rows.push({ key, button: b.id, event: e, inputLabel: inputLabel(b, i), bridgeRule: b.bridge_rule, mappings: hb.mapping.filter((m) => m.button === b.id && m.event === e) });
    }
  });
  for (const m of hb.mapping) {
    const key = `${m.button}|${m.event}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ key, button: m.button, event: m.event, inputLabel: 'Onbekende knop', bridgeRule: hb.bridge_rules.includes(m.button), mappings: hb.mapping.filter((x) => x.button === m.button && x.event === m.event) });
  }
  return rows;
}
