import '../climate/climate.css';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { ScheduleBlock, Zone, ZoneHistory } from '../api/types';
import { useHome } from '../state';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';
import { Tabs, tabId } from '../components/Tabs';
import { ZoneControlPanel } from '../components/zone';
import { ENERGY_PRIORITY_LABEL, t as label } from '../i18n';
import { temp, time } from '../format';
import { FormError, SafeLink, storeZone, useClimateCan } from '../climate/common';
import { ScheduleEditor } from '../climate/ScheduleEditor';
import { PolicyForm } from '../climate/PolicyForm';
import { ProfilesPanel } from '../climate/Profiles';
import { modeEffect, todaySegments, weekdayIndex } from '../climate/schedule';

// Kept here for existing imports (tests): the logic lives in climate/schedule.ts.
export { todaySegments, weekdayIndex } from '../climate/schedule';
export type { Segment } from '../climate/schedule';

function ScheduleBar({ zoneId, tz }: { zoneId: string; tz?: string }) {
  const q = useQuery({ queryKey: qk.schedule(zoneId), queryFn: () => api.zoneSchedule(zoneId) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  const segs = todaySegments(q.data, weekdayIndex(new Date(), tz));
  if (!segs.length) return <Empty>Geen schema ingesteld voor vandaag.</Empty>;
  const temps = segs.map((s) => s.target_c);
  const mid = (Math.min(...temps) + Math.max(...temps)) / 2;
  const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  return (
    <>
      <div className="schedule-bar" role="img" aria-label={`Vandaag: ${segs.map((s) => `${hhmm(s.from)} ${s.label} ${temp(s.target_c)}`).join(', ')}`}>
        {segs.map((s, i) => (
          <div key={i} className={s.target_c > mid || temps.every((x) => x === mid) ? 'warm' : 'cold'} style={{ flex: s.to - s.from }} title={`${s.label} ${temp(s.target_c)}`}>
            {s.to - s.from >= 180 ? `${s.label} ${temp(s.target_c)}` : ''}
          </div>
        ))}
      </div>
      <div className="axis" aria-hidden="true"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
    </>
  );
}

export function HistoryChart({ history }: { history: ZoneHistory }) {
  const all = [...history.temperature, ...history.target];
  if (!all.length) return <Empty>Nog geen geschiedenis.</Empty>;
  const ts = all.map(([tt]) => new Date(tt).getTime());
  const vs = all.map(([, v]) => v);
  const t0 = Math.min(...ts), t1 = Math.max(...ts, t0 + 1);
  const v0 = Math.floor(Math.min(...vs) - 0.5), v1 = Math.ceil(Math.max(...vs) + 0.5);
  const W = 1000, H = 180;
  const x = (tt: string) => ((new Date(tt).getTime() - t0) / (t1 - t0)) * W;
  const y = (v: number) => H - ((v - v0) / (v1 - v0)) * (H - 10) - 5;
  const line = history.temperature.map(([tt, v], i) => `${i ? 'L' : 'M'}${x(tt).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const step = history.target.map(([tt, v], i) => (i ? `H${x(tt).toFixed(1)} V${y(v).toFixed(1)}` : `M${x(tt).toFixed(1)} ${y(v).toFixed(1)}`)).join(' ') + (history.target.length ? ` H${W}` : '');
  const last = history.temperature.at(-1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 180 }} role="img" aria-label={`Gemeten en doeltemperatuur, bereik ${v0}° tot ${v1}°${last ? `, laatste meting ${temp(last[1])}` : ''}`} preserveAspectRatio="none">
      {[0.25, 0.5, 0.75].map((f) => <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} stroke="var(--border)" vectorEffect="non-scaling-stroke" />)}
      <path d={step} fill="none" stroke="var(--heat)" strokeWidth="2.5" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />
      <path d={line} fill="none" stroke="var(--text)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Admin settings for one zone: which unit and sensor, and the regulation limits (incl. item 45 fields). */
export function ZoneSettings({ zone, homeId }: { zone: Zone; homeId: string }) {
  const qc = useQueryClient();
  const devices = useQuery({ queryKey: qk.devices(homeId), queryFn: () => api.devices(homeId) });
  const list = Array.isArray(devices.data) ? devices.data : [];
  const units = list.filter((d) => d.kind === 'ac_unit' && d.lifecycle !== 'archived');
  const sensors = list.filter((d) => d.kind === 'zigbee_sensor' && d.lifecycle !== 'archived');
  const [unit, setUnit] = useState(zone.unit_device_id ?? '');
  const [sensor, setSensor] = useState(zone.sensor_device_id ?? '');
  // Prefill from the server (zone_view returns the current binding) and follow external changes.
  useEffect(() => setUnit(zone.unit_device_id ?? ''), [zone.unit_device_id]);
  useEffect(() => setSensor(zone.sensor_device_id ?? ''), [zone.sensor_device_id]);
  const link = useMutation({
    mutationFn: () => api.patchZone(zone.id, { unit_device_id: unit || null, sensor_device_id: sensor || null }),
    onSuccess: (z) => {
      storeZone(qc, homeId, z);
      qc.invalidateQueries({ queryKey: qk.zoneLog(z.id) });
    },
  });
  return (
    <div className="c-grid">
      <section aria-labelledby={`link-h-${zone.id}`} className="card">
        <h2 id={`link-h-${zone.id}`} style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Zone-koppeling</h2>
        <p className="small muted" style={{ margin: 0 }}>
          Kies de airco-unit en de externe sensor voor deze zone. Hang de sensor op een representatieve plek, niet in de luchtstroom of in de zon.
        </p>
        {devices.isLoading ? <Loading /> : (
          <form className="form-grid" onSubmit={(e) => { e.preventDefault(); link.mutate(); }}>
            <label className="field">Airco-unit
              <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                <option value="">Geen</option>
                {zone.unit_device_id && !units.some((d) => d.id === zone.unit_device_id) && <option value={zone.unit_device_id}>Huidige unit (staat niet in de lijst)</option>}
                {units.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
            <label className="field">Temperatuursensor
              <select value={sensor} onChange={(e) => setSensor(e.target.value)}>
                <option value="">Sensor van de unit zelf</option>
                {zone.sensor_device_id && !sensors.some((d) => d.id === zone.sensor_device_id) && <option value={zone.sensor_device_id}>Huidige sensor (staat niet in de lijst)</option>}
                {sensors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
            <ActionButton type="submit" className="btn primary" disabled={link.isPending}>Opslaan</ActionButton>
          </form>
        )}
        {!devices.isLoading && units.length === 0 && (
          <p className="small muted" style={{ margin: 0 }}>Nog geen airco-unit gevonden. Koppel er een via <SafeLink to="/apparaten">Apparaten</SafeLink>.</p>
        )}
        {link.isSuccess && <span className="small muted" role="status">Koppeling opgeslagen.</span>}
        <FormError error={link.error} />
      </section>
      <section aria-labelledby={`pol-h-${zone.id}`} className="card">
        <h2 id={`pol-h-${zone.id}`} style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Regelgrenzen</h2>
        <p className="small muted" style={{ margin: 0 }}>Deze grenzen voorkomen pendelen. De beveiligingen van de airco zelf blijven altijd leidend.</p>
        <PolicyForm zone={zone} homeId={homeId} />
      </section>
    </div>
  );
}

function signed(v: number): string {
  if (!v) return '0°';
  return `${v > 0 ? '+' : '−'}${Math.abs(v).toLocaleString('nl-NL', { maximumFractionDigits: 2 })}°`;
}

function TodayCard({ zone, tz }: { zone: Zone; tz?: string }) {
  const p = zone.policy;
  const nc = zone.next_change;
  return (
    <section aria-labelledby={`sch-h-${zone.id}`} className="card" style={{ gap: 16 }}>
      <div className="section-head">
        <h2 id={`sch-h-${zone.id}`} style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Schema vandaag</h2>
        {nc && (
          <span className="small muted">
            Volgende: {time(nc.at, tz)} · {nc.label || 'blok'} {nc.mode === 'off' ? 'uit' : temp(nc.target_c)} · {modeEffect(nc.mode)}
          </span>
        )}
      </div>
      <ScheduleBar zoneId={zone.id} tz={tz} />
      {zone.watchdog_since && (
        <div className="note warn">Waakhond: de temperatuur reageert niet sinds {time(zone.watchdog_since, tz)}. Controleer of ramen of deuren openstaan en of de airco echt draait.</div>
      )}
      <dl className="facts">
        <div><dt>Comfortband</dt><dd>± {p.band_c.toLocaleString('nl-NL')}°</dd></div>
        <div><dt>Energieprioriteit</dt><dd>{label(ENERGY_PRIORITY_LABEL, zone.energy_priority)}</dd></div>
        <div><dt>Profiel</dt><dd>{zone.profile ?? 'eigen schema'}</dd></div>
        <div><dt>Bijsturing door sensor</dt><dd>{signed(zone.correction_c)}</dd></div>
        <div><dt>Min. tijd tussen wijzigingen</dt><dd>{Math.round(p.min_interval_s / 60)} min</dd></div>
        <div><dt>Waakhond</dt><dd>{p.watchdog_minutes ? `na ${p.watchdog_minutes} min` : 'uit'}</dd></div>
      </dl>
    </section>
  );
}

function ControlTab({ zone, tz, onLink }: { zone: Zone; tz?: string; onLink?: () => void }) {
  const history = useQuery({ queryKey: qk.history(zone.id), queryFn: () => api.zoneHistory(zone.id, 24), refetchInterval: 300_000 });
  const log = useQuery({ queryKey: qk.zoneLog(zone.id), queryFn: () => api.zoneLog(zone.id, 20), refetchInterval: 120_000 });
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="c-grid">
        <ZoneControlPanel zone={zone} tz={tz} onLink={onLink} />
        <TodayCard zone={zone} tz={tz} />
      </div>
      <section aria-labelledby={`gesch-h-${zone.id}`} className="card">
        <div className="section-head">
          <h2 id={`gesch-h-${zone.id}`} style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Laatste 24 uur</h2>
          <div className="legend">
            <span><span className="swatch" style={{ background: 'var(--text)', height: 3 }} />Gemeten</span>
            <span><span className="swatch" style={{ background: 'var(--heat)', height: 3 }} />Doel</span>
          </div>
        </div>
        {history.data ? <HistoryChart history={history.data} /> : history.isLoading ? <Loading /> : <ErrorNote error={history.error} />}
        <h3 style={{ marginTop: 8, fontSize: 14 }}>Aanpassingslog</h3>
        {log.data?.length ? (
          <ul className="log">
            {log.data.map((l, i) => (
              <li key={i}><span className="t">{time(l.at, tz)}</span><span>{l.message}</span></li>
            ))}
          </ul>
        ) : (
          <Empty>Nog geen aanpassingen.</Empty>
        )}
      </section>
    </div>
  );
}

function ScheduleTab({ zone, tz, homeId }: { zone: Zone; tz?: string; homeId: string }) {
  const qc = useQueryClient();
  const { admin } = useClimateCan();
  const q = useQuery({ queryKey: qk.schedule(zone.id), queryFn: () => api.zoneSchedule(zone.id) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  const save = async (blocks: ScheduleBlock[]) => {
    const s = await api.putZoneSchedule(zone.id, { blocks });
    qc.setQueryData(qk.schedule(zone.id), s);
    qc.invalidateQueries({ queryKey: qk.zone(zone.id) });
    qc.invalidateQueries({ queryKey: qk.zones(homeId) });
    qc.invalidateQueries({ queryKey: qk.overview(homeId) });
    return s;
  };
  const range = zone.unit_linked ? { min_c: zone.capabilities.min_c, max_c: zone.capabilities.max_c, modes: zone.capabilities.modes } : null;
  const profileName = q.data.profile ?? (zone.profile_id ? zone.profile : null);
  return (
    <section aria-labelledby={`week-h-${zone.id}`} className="card">
      <div className="section-head">
        <h2 id={`week-h-${zone.id}`} style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Weekschema {zone.name}</h2>
        {!admin && <span className="small muted">Alleen een beheerder kan het schema wijzigen.</span>}
      </div>
      <ScheduleEditor
        idBase={`sched-${zone.id}`}
        blocks={q.data.blocks}
        range={range}
        tz={tz}
        editable={admin}
        onSave={save}
        intro={
          <>
            {profileName && (
              <div className="note info">
                {zone.name} volgt nu profiel {profileName}. {admin ? 'Sla je hier een schema op, dan wordt het een eigen schema en volgt de zone het profiel niet meer.' : ''}
              </div>
            )}
            {!zone.unit_linked && <div className="note info">Er is nog geen airco gekoppeld; het schema wordt gebruikt zodra dat wel zo is.</div>}
          </>
        }
      />
    </section>
  );
}

type Section = 'bediening' | 'schema' | 'profielen' | 'instellingen';

function ZoneDetail({ zone, zones, tz, homeId, section, onSection }: { zone: Zone; zones: Zone[]; tz?: string; homeId: string; section: Section; onSection: (s: Section) => void }) {
  const { admin } = useClimateCan();
  const items: { key: Section; label: string }[] = [
    { key: 'bediening', label: 'Bediening' },
    { key: 'schema', label: 'Weekschema' },
    { key: 'profielen', label: 'Comfortprofielen' },
    ...(admin ? [{ key: 'instellingen' as Section, label: 'Instellingen' }] : []),
  ];
  const current = items.some((i) => i.key === section) ? section : 'bediening';
  return (
    <Tabs label={`Onderdelen van ${zone.name}`} items={items} value={current} onChange={onSection} idBase={`zone-${zone.id}`}>
      {(key) =>
        key === 'schema' ? <ScheduleTab zone={zone} tz={tz} homeId={homeId} />
        : key === 'profielen' ? <ProfilesPanel homeId={homeId} zone={zone} zones={zones} tz={tz} />
        : key === 'instellingen' ? <ZoneSettings zone={zone} homeId={homeId} />
        : <ControlTab zone={zone} tz={tz} onLink={admin ? () => onSection('instellingen') : undefined} />
      }
    </Tabs>
  );
}

export function ClimatePage() {
  const { homeId } = useHome();
  const [params, setParams] = useSearchParams();
  const zones = useQuery({ queryKey: qk.zones(homeId ?? ''), queryFn: () => api.zones(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  const homes = useQuery({ queryKey: qk.homes, queryFn: api.homes });
  const tz = Array.isArray(homes.data) ? homes.data.find((h) => h.id === homeId)?.timezone : undefined;
  if (!homeId || zones.isLoading) return <Loading />;
  if (!zones.data) return <ErrorNote error={zones.error} />;
  const list = zones.data;
  const zone = list.find((z) => z.id === params.get('zone')) ?? list[0];
  const section = (params.get('tab') as Section | null) ?? 'bediening';
  const go = (zoneId: string, tab: Section) => setParams({ zone: zoneId, tab }, { replace: true });

  return (
    <>
      <header className="page-head">
        <div>
          <span className="eyebrow">Klimaat · zone</span>
          <h1>{zone?.name ?? 'Klimaat'}</h1>
        </div>
        {list.length > 1 && zone && (
          <Tabs label="Zone" items={list.map((z) => ({ key: z.id, label: z.name }))} value={zone.id} onChange={(id) => go(id, section)} idBase="zones" />
        )}
      </header>
      {zone ? (
        <div role={list.length > 1 ? 'tabpanel' : undefined} aria-labelledby={list.length > 1 ? tabId('zones', zone.id) : undefined}>
          <ZoneDetail key={zone.id} zone={zone} zones={list} tz={tz} homeId={homeId} section={section} onSection={(s) => go(zone.id, s)} />
        </div>
      ) : (
        <Empty>
          Nog geen klimaatzones. Koppel eerst de airco-units via de <SafeLink to="/installatie">installatiewizard</SafeLink> of <SafeLink to="/apparaten">Apparaten</SafeLink>.
        </Empty>
      )}
    </>
  );
}
