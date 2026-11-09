// Comfort profiles (verbeterlijst 45): a named comfort setting (target, band, mode, energy priority,
// optional week schedule) that can be applied to a zone. Editing a profile re-applies it to the zones
// that follow it; saving an own schedule for a zone detaches it from its profile.
import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { EnergyPriority, HvacMode, Profile, ProfileIn, ScheduleBlock, Zone } from '../api/types';
import { MODE_LABEL, temp } from '../format';
import { ENERGY_PRIORITY_LABEL } from '../i18n';
import { ActionButton, Empty, Loading } from '../components/bits';
import { FormError, num, storeZone, useClimateCan } from './common';
import { ScheduleEditor } from './ScheduleEditor';
import { PRIORITY_HELP } from './PolicyForm';

const MODES: HvacMode[] = ['heat', 'cool', 'auto', 'dry', 'fan', 'off'];

type ScheduleSource = 'keep' | 'none' | 'zone';

interface Draft {
  name: string;
  target: string;
  band: string;
  mode: HvacMode | '';
  priority: EnergyPriority;
  source: ScheduleSource;
}

const nlNum = (v: number) => String(v).replace('.', ',');

function draftOf(p?: Profile): Draft {
  return p
    ? { name: p.name, target: nlNum(p.target_c), band: nlNum(p.band_c), mode: p.mode ?? '', priority: p.energy_priority, source: 'keep' }
    : { name: '', target: '21', band: '0,5', mode: '', priority: 'balanced', source: 'none' };
}

/** Validate a profile form; returns the body or Dutch problems. */
export function profileBody(d: Draft, existing: ScheduleBlock[], zoneBlocks: ScheduleBlock[] | null): { body: ProfileIn | null; problems: string[] } {
  const problems: string[] = [];
  const name = d.name.trim();
  const target = num(d.target);
  const band = num(d.band);
  if (!name) problems.push('Geef het profiel een naam.');
  if (name.length > 64) problems.push('De naam is te lang (maximaal 64 tekens).');
  if (!Number.isFinite(target) || target < 10 || target > 30) problems.push('Doeltemperatuur: kies 10 tot 30 °C.');
  if (!Number.isFinite(band) || band < 0.2 || band > 3) problems.push('Comfortband: kies 0,2 tot 3 °C.');
  if (problems.length) return { body: null, problems };
  const schedule = d.source === 'keep' ? existing : d.source === 'zone' ? zoneBlocks ?? [] : [];
  return { body: { name, target_c: target, band_c: band, mode: d.mode || null, energy_priority: d.priority, schedule }, problems };
}

function ProfileForm({ profile, homeId, zone, zoneBlocks, onDone }: { profile?: Profile; homeId: string; zone: Zone; zoneBlocks: ScheduleBlock[] | null; onDone: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(() => draftOf(profile));
  const [problems, setProblems] = useState<string[]>([]);
  const save = useMutation({
    mutationFn: (body: ProfileIn) => (profile ? api.updateProfile(profile.id, body) : api.createProfile(homeId, body)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] });
      qc.invalidateQueries({ queryKey: ['zones'] });
      qc.invalidateQueries({ queryKey: ['schedule'] });
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = profileBody(d, profile?.schedule ?? [], zoneBlocks);
    setProblems(r.problems);
    if (r.body) save.mutate(r.body);
  };
  const idp = profile ? profile.id : 'new';
  return (
    <form className="card stack" onSubmit={submit} noValidate aria-labelledby={`pf-h-${idp}`}>
      <h3 id={`pf-h-${idp}`}>{profile ? `Profiel ${profile.name} wijzigen` : 'Nieuw comfortprofiel'}</h3>
      {profile && profile.zone_ids.length > 0 && (
        <div className="note info">Zones die dit profiel volgen krijgen de wijziging meteen (ook hun weekschema).</div>
      )}
      <div className="form-grid">
        <label className="field">Naam van het profiel<input required maxLength={64} value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="bijv. Thuiswerkdag" /></label>
        <label className="field">Doeltemperatuur °C<input inputMode="decimal" required value={d.target} onChange={(e) => setD({ ...d, target: e.target.value })} /></label>
        <label className="field">Comfortband ± °C<input inputMode="decimal" required value={d.band} onChange={(e) => setD({ ...d, band: e.target.value })} /></label>
        <label className="field">Modus van het profiel
          <select value={d.mode} onChange={(e) => setD({ ...d, mode: e.target.value as HvacMode | '' })}>
            <option value="">Houd huidige modus</option>
            {MODES.map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="choice">
        <legend>Energieprioriteit</legend>
        {(['comfort', 'balanced', 'saving'] as EnergyPriority[]).map((k) => (
          <label key={k} className={`choice-card${d.priority === k ? ' selected' : ''}`}>
            <input type="radio" name={`pf-prio-${idp}`} checked={d.priority === k} onChange={() => setD({ ...d, priority: k })} />
            <span><strong>{ENERGY_PRIORITY_LABEL[k]}</strong><span className="small muted">{PRIORITY_HELP[k]}</span></span>
          </label>
        ))}
      </fieldset>
      <fieldset className="choice">
        <legend>Weekschema</legend>
        {profile && (
          <label className={`choice-card${d.source === 'keep' ? ' selected' : ''}`}>
            <input type="radio" name={`pf-src-${idp}`} checked={d.source === 'keep'} onChange={() => setD({ ...d, source: 'keep' })} />
            <span><strong>Huidig schema houden</strong><span className="small muted">{profile.schedule.length ? `${profile.schedule.length} blokken` : 'Geen eigen weekschema'}</span></span>
          </label>
        )}
        <label className={`choice-card${d.source === 'none' ? ' selected' : ''}`}>
          <input type="radio" name={`pf-src-${idp}`} checked={d.source === 'none'} onChange={() => setD({ ...d, source: 'none' })} />
          <span><strong>Geen weekschema</strong><span className="small muted">De hele week de doeltemperatuur van het profiel.</span></span>
        </label>
        <label className={`choice-card${d.source === 'zone' ? ' selected' : ''}`}>
          <input type="radio" name={`pf-src-${idp}`} checked={d.source === 'zone'} disabled={!zoneBlocks} onChange={() => setD({ ...d, source: 'zone' })} />
          <span><strong>Weekschema van {zone.name} overnemen</strong><span className="small muted">{zoneBlocks ? `${zoneBlocks.length} blokken` : 'Schema wordt geladen…'}</span></span>
        </label>
      </fieldset>
      {problems.length > 0 && (
        <div className="note error" role="alert">{problems.length === 1 ? problems[0] : <ul className="plain-list">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}</div>
      )}
      <FormError error={save.error} />
      <div className="c-actions">
        <ActionButton type="submit" className="btn primary" disabled={save.isPending}>{profile ? 'Profiel opslaan' : 'Profiel aanmaken'}</ActionButton>
        <button type="button" className="btn" onClick={onDone}>Annuleren</button>
      </div>
    </form>
  );
}

function ProfileCard({ profile, zone, zones, zoneBlocks, homeId, tz }: { profile: Profile; zone: Zone; zones: Zone[]; zoneBlocks: ScheduleBlock[] | null; homeId: string; tz?: string }) {
  const qc = useQueryClient();
  const { admin } = useClimateCan();
  const [mode, setMode] = useState<'view' | 'edit' | 'schedule'>('view');
  const following = zone.profile_id === profile.id;
  const apply = useMutation({
    mutationFn: () => api.applyProfile(zone.id, profile.id),
    onSuccess: (z) => {
      storeZone(qc, homeId, z);
      qc.invalidateQueries({ queryKey: qk.schedule(zone.id) });
      qc.invalidateQueries({ queryKey: qk.profiles(homeId) });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteProfile(profile.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.profiles(homeId) });
      qc.invalidateQueries({ queryKey: ['zones'] });
    },
  });
  const saveSchedule = (blocks: ScheduleBlock[]) =>
    api
      .updateProfile(profile.id, { name: profile.name, target_c: profile.target_c, band_c: profile.band_c, mode: profile.mode, energy_priority: profile.energy_priority, schedule: blocks })
      .then((p) => {
        qc.setQueryData<Profile[]>(qk.profiles(homeId), (l) => l?.map((x) => (x.id === p.id ? p : x)));
        qc.invalidateQueries({ queryKey: ['schedule'] });
        return p;
      });
  const users = zones.filter((z) => profile.zone_ids.includes(z.id)).map((z) => z.name);

  if (mode === 'edit') return <ProfileForm profile={profile} homeId={homeId} zone={zone} zoneBlocks={zoneBlocks} onDone={() => setMode('view')} />;

  return (
    <article className="card" aria-labelledby={`pr-h-${profile.id}`}>
      <div className="section-head" style={{ alignItems: 'center' }}>
        <h3 id={`pr-h-${profile.id}`}>{profile.name}</h3>
        {following && <span className="pill ok">{zone.name} volgt dit profiel</span>}
      </div>
      <div className="c-pills">
        <span className="pill neutral">Doel {temp(profile.target_c)}</span>
        <span className="pill neutral">Band ±{nlNum(profile.band_c)}°</span>
        <span className="pill neutral">{profile.mode ? `Modus ${(MODE_LABEL[profile.mode] ?? profile.mode).toLowerCase()}` : 'Houdt huidige modus'}</span>
        <span className="pill neutral">{ENERGY_PRIORITY_LABEL[profile.energy_priority] ?? profile.energy_priority}</span>
      </div>
      <span className="small muted">
        {profile.schedule.length ? `Weekschema met ${profile.schedule.length} ${profile.schedule.length === 1 ? 'blok' : 'blokken'}.` : `Geen weekschema: de hele week ${temp(profile.target_c)}.`}{' '}
        {users.length ? `Gebruikt door: ${users.join(', ')}.` : 'Nog door geen enkele zone gebruikt.'}
      </span>
      {admin && (
        <div className="c-actions">
          <ActionButton
            className="btn primary"
            disabled={following || apply.isPending}
            onClick={() => {
              if (window.confirm(`Het weekschema van ${zone.name} wordt vervangen door profiel ${profile.name}. Een handmatige instelling blijft lopen tot hij afloopt. Doorgaan?`)) apply.mutate();
            }}
          >
            {following ? `Actief voor ${zone.name}` : `Toepassen op ${zone.name}`}
          </ActionButton>
          <button type="button" className="btn" onClick={() => setMode('edit')}>Wijzigen</button>
          <button type="button" className="btn" aria-expanded={mode === 'schedule'} onClick={() => setMode(mode === 'schedule' ? 'view' : 'schedule')}>
            {mode === 'schedule' ? 'Weekschema sluiten' : 'Weekschema bewerken'}
          </button>
          <ActionButton
            className="btn danger"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`Profiel ${profile.name} verwijderen? Zones houden hun huidige schema.`)) remove.mutate();
            }}
          >
            Verwijderen
          </ActionButton>
        </div>
      )}
      {apply.isSuccess && <span className="small muted" role="status">Profiel toegepast op {zone.name}.</span>}
      <FormError error={apply.error ?? remove.error} />
      {mode === 'schedule' && (
        <ScheduleEditor
          idBase={`prs-${profile.id}`}
          blocks={profile.schedule}
          range={null}
          tz={tz}
          editable={admin}
          onSave={saveSchedule}
          saveLabel="Weekschema van profiel opslaan"
          intro={<p className="small muted" style={{ margin: 0 }}>Zonder blokken krijgt een zone met dit profiel de hele week {temp(profile.target_c)}. Blokken zonder modus nemen de modus van het profiel over.</p>}
        />
      )}
    </article>
  );
}

export function ProfilesPanel({ homeId, zone, zones, tz }: { homeId: string; zone: Zone; zones: Zone[]; tz?: string }) {
  const { admin } = useClimateCan();
  const list = useQuery({ queryKey: qk.profiles(homeId), queryFn: () => api.profiles(homeId) });
  const sched = useQuery({ queryKey: qk.schedule(zone.id), queryFn: () => api.zoneSchedule(zone.id) });
  const [creating, setCreating] = useState(false);
  const zoneBlocks = sched.data?.blocks ?? null;
  const current = list.data?.find((p) => p.id === zone.profile_id);
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Een comfortprofiel bundelt doeltemperatuur, comfortband, modus, energieprioriteit en eventueel een weekschema. Toepassen vervangt het weekschema van de zone; past je het schema van de zone daarna zelf aan, dan volgt de zone het profiel niet meer.
      </p>
      <div className="note info">
        {current ? `${zone.name} volgt profiel ${current.name}.` : zone.profile ? `${zone.name} heeft een eigen schema (label „${zone.profile}”) en volgt geen profiel.` : `${zone.name} heeft een eigen schema en volgt geen profiel.`}
      </div>
      {list.isLoading && <Loading />}
      <FormError error={list.error} />
      {list.data?.length === 0 && <Empty>Nog geen comfortprofielen.</Empty>}
      <div className="c-grid">
        {list.data?.map((p) => <ProfileCard key={p.id} profile={p} zone={zone} zones={zones} zoneBlocks={zoneBlocks} homeId={homeId} tz={tz} />)}
      </div>
      {admin && (creating ? (
        <ProfileForm homeId={homeId} zone={zone} zoneBlocks={zoneBlocks} onDone={() => setCreating(false)} />
      ) : (
        <div><button type="button" className="btn" onClick={() => setCreating(true)}>Nieuw profiel</button></div>
      ))}
    </div>
  );
}
