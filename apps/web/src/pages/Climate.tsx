import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Schedule, Zone, ZoneHistory, ZonePolicy } from '../api/types';
import { useIsAdmin } from '../user';
import { useCanAct, useHome } from '../state';
import { ActionButton, Empty, ErrorNote, Loading, QualityBadge } from '../components/bits';
import { ModeButtons, PendingLine, useZoneCommand, measurementLine } from '../components/zone';
import { CONTROL_LABEL, temp, time } from '../format';

/** Weekday index used by the schedule API: 0 = maandag … 6 = zondag (Python weekday()). */
export function weekdayIndex(d: Date, tz?: string): number {
  const name = d.toLocaleDateString('en-US', { weekday: 'short', timeZone: tz });
  return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(name);
}

const toMin = (hhmm: string) => {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
};

export interface Segment { from: number; to: number; target_c: number; label: string }

/** Turn the weekly schedule into today's segments (minutes since midnight). */
export function todaySegments(s: Schedule, day: number): Segment[] {
  const prev = (day + 6) % 7;
  const today = s.blocks.filter((b) => b.days.includes(day)).sort((a, b) => toMin(a.start) - toMin(b.start));
  const carry = s.blocks.filter((b) => b.days.includes(prev)).sort((a, b) => toMin(a.start) - toMin(b.start)).at(-1);
  const segs: Segment[] = [];
  if (carry && (!today.length || toMin(today[0].start) > 0)) {
    segs.push({ from: 0, to: today.length ? toMin(today[0].start) : 1440, target_c: carry.target_c, label: carry.label });
  }
  today.forEach((b, i) => {
    segs.push({ from: toMin(b.start), to: i + 1 < today.length ? toMin(today[i + 1].start) : 1440, target_c: b.target_c, label: b.label });
  });
  return segs.filter((x) => x.to > x.from);
}

function ScheduleBar({ zoneId, tz }: { zoneId: string; tz?: string }) {
  const q = useQuery({ queryKey: ['schedule', zoneId], queryFn: () => api.zoneSchedule(zoneId) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorNote error={q.error} />;
  const segs = todaySegments(q.data, weekdayIndex(new Date(), tz));
  if (!segs.length) return <Empty>Geen schema ingesteld voor vandaag.</Empty>;
  const temps = segs.map((s) => s.target_c);
  const mid = (Math.min(...temps) + Math.max(...temps)) / 2;
  return (
    <>
      <div className="schedule-bar" role="img" aria-label={segs.map((s) => `${String(Math.floor(s.from / 60)).padStart(2, '0')}:${String(s.from % 60).padStart(2, '0')} ${s.label} ${temp(s.target_c)}`).join(', ')}>
        {segs.map((s, i) => (
          <div key={i} className={s.target_c > mid || temps.every((t) => t === mid) ? 'warm' : 'cold'} style={{ flex: s.to - s.from }} title={`${s.label} ${temp(s.target_c)}`}>
            {(s.to - s.from) >= 180 ? `${s.label} ${temp(s.target_c)}` : ''}
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
  const ts = all.map(([t]) => new Date(t).getTime());
  const vs = all.map(([, v]) => v);
  const t0 = Math.min(...ts), t1 = Math.max(...ts, t0 + 1);
  const v0 = Math.floor(Math.min(...vs) - 0.5), v1 = Math.ceil(Math.max(...vs) + 0.5);
  const W = 1000, H = 180;
  const x = (t: string) => ((new Date(t).getTime() - t0) / (t1 - t0)) * W;
  const y = (v: number) => H - ((v - v0) / (v1 - v0)) * (H - 10) - 5;
  const line = history.temperature.map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const step = history.target.map(([t, v], i) => (i ? `H${x(t).toFixed(1)} V${y(v).toFixed(1)}` : `M${x(t).toFixed(1)} ${y(v).toFixed(1)}`)).join(' ') + (history.target.length ? ` H${W}` : '');
  const last = history.temperature.at(-1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 180 }} role="img" aria-label={`Gemeten en doeltemperatuur, bereik ${v0}° tot ${v1}°${last ? `, laatste meting ${temp(last[1])}` : ''}`} preserveAspectRatio="none">
      {[0.25, 0.5, 0.75].map((f) => <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} stroke="var(--border)" vectorEffect="non-scaling-stroke" />)}
      <path d={step} fill="none" stroke="var(--heat)" strokeWidth="2.5" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />
      <path d={line} fill="none" stroke="var(--text)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function ZoneSettings({ zone, homeId }: { zone: Zone; homeId: string }) {
  const qc = useQueryClient();
  const devices = useQuery({ queryKey: ['devices', homeId], queryFn: () => api.devices(homeId) });
  const units = devices.data?.filter((d) => d.kind === 'ac_unit') ?? [];
  const sensors = devices.data?.filter((d) => d.kind === 'zigbee_sensor') ?? [];
  const [unit, setUnit] = useState(zone.unit_device_id ?? '');
  const [sensor, setSensor] = useState(zone.sensor_device_id ?? '');
  // Prefill from the server (zone_view returns the current binding) and follow external changes.
  useEffect(() => setUnit(zone.unit_device_id ?? ''), [zone.unit_device_id]);
  useEffect(() => setSensor(zone.sensor_device_id ?? ''), [zone.sensor_device_id]);
  const [pol, setPol] = useState({
    band_c: String(zone.policy.band_c),
    min_interval_min: String(Math.round(zone.policy.min_interval_s / 60)),
    max_step_c: String(zone.policy.max_step_c),
    max_starts_per_hour: String(zone.policy.max_starts_per_hour),
    stale_after_min: String(Math.round(zone.policy.stale_after_s / 60)),
  });
  const onSaved = (z: Zone) => {
    qc.setQueryData(['zone', z.id], z);
    qc.invalidateQueries({ queryKey: ['zones'] });
    qc.invalidateQueries({ queryKey: ['zonelog', z.id] });
  };
  const link = useMutation({ mutationFn: () => api.patchZone(zone.id, { unit_device_id: unit || null, sensor_device_id: sensor || null }), onSuccess: onSaved });
  const num = (v: string) => Number(v.replace(',', '.'));
  const policy = useMutation({
    mutationFn: () => {
      const p: ZonePolicy = {
        band_c: num(pol.band_c),
        min_interval_s: Math.round(num(pol.min_interval_min) * 60),
        max_step_c: num(pol.max_step_c),
        max_starts_per_hour: Math.round(num(pol.max_starts_per_hour)),
        stale_after_s: Math.round(num(pol.stale_after_min) * 60),
      };
      return api.putZonePolicy(zone.id, p);
    },
    onSuccess: onSaved,
  });
  const field = (k: keyof typeof pol, label: string, min: number, max: number, step: number) => (
    <label className="field">{label}
      <input type="number" min={min} max={max} step={step} required value={pol[k]} onChange={(e) => setPol({ ...pol, [k]: e.target.value })} />
    </label>
  );
  return (
    <div className="grid-auto">
      <section aria-labelledby="link-h" className="card">
        <h2 id="link-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Zone-koppeling</h2>
        <p className="small muted" style={{ margin: 0 }}>Kies de airco-unit en de externe sensor voor deze zone. Hang de sensor op een representatieve plek, niet in de luchtstroom of in de zon.</p>
        {devices.isLoading ? <Loading /> : (
          <form className="form-grid" onSubmit={(e) => { e.preventDefault(); link.mutate(); }}>
            <label className="field">Airco-unit
              <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                <option value="">Geen</option>
                {units.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
            <label className="field">Temperatuursensor
              <select value={sensor} onChange={(e) => setSensor(e.target.value)}>
                <option value="">Sensor van de unit zelf</option>
                {sensors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
            <ActionButton type="submit" className="btn primary" disabled={link.isPending}>Opslaan</ActionButton>
          </form>
        )}
        {link.isSuccess && <span className="small muted" role="status">Koppeling opgeslagen.</span>}
        {link.error && <ErrorNote error={link.error} />}
      </section>
      <section aria-labelledby="pol-h" className="card">
        <h2 id="pol-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Regelgrenzen</h2>
        <p className="small muted" style={{ margin: 0 }}>Deze grenzen voorkomen pendelen. De beveiligingen van de airco zelf blijven altijd leidend.</p>
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); policy.mutate(); }}>
          {field('band_c', 'Comfortband ± °C', 0.2, 3, 0.1)}
          {field('min_interval_min', 'Min. tijd tussen wijzigingen (min)', 5, 120, 1)}
          {field('max_step_c', 'Max. setpoint-sprong °C', 0.5, 3, 0.5)}
          {field('max_starts_per_hour', 'Max. starts per uur', 1, 6, 1)}
          {field('stale_after_min', 'Sensor verouderd na (min)', 5, 120, 1)}
          <ActionButton type="submit" className="btn primary" disabled={policy.isPending}>Opslaan</ActionButton>
        </form>
        {policy.isSuccess && <span className="small muted" role="status">Regelgrenzen opgeslagen.</span>}
        {policy.error && <ErrorNote error={policy.error} />}
      </section>
    </div>
  );
}

function ZoneDetail({ zone, tz, homeId }: { zone: Zone; tz?: string; homeId: string }) {
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const canAct = useCanAct();
  const cmd = useZoneCommand(zone);
  const history = useQuery({ queryKey: ['history', zone.id], queryFn: () => api.zoneHistory(zone.id, 24), refetchInterval: 300_000 });
  const log = useQuery({ queryKey: ['zonelog', zone.id], queryFn: () => api.zoneLog(zone.id, 20), refetchInterval: 120_000 });
  const resume = useMutation({
    mutationFn: () => api.zoneResume(zone.id),
    onSuccess: (z) => {
      qc.setQueryData(['zone', z.id], z);
      qc.invalidateQueries({ queryKey: ['zones'] });
    },
  });
  const t = zone.temperature;
  const controllable = canAct && zone.online && zone.mode !== 'off';
  const p = zone.policy;

  return (
    <>
      <div className="grid-auto">
        <section aria-labelledby="bed-h" className="card live" style={{ padding: 24, gap: 20 }}>
          <div className="section-head">
            <h2 id="bed-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Bediening</h2>
            <span className="small muted">{zone.override ? `Handmatig${zone.override.until ? ` tot ${time(zone.override.until, tz)}` : ''}` : CONTROL_LABEL[zone.control]}</span>
          </div>
          <div className="row" style={{ justifyContent: 'center', gap: 28, flexWrap: 'nowrap' }}>
            <button type="button" className="icon-btn lg" aria-label="Doeltemperatuur lager" disabled={!controllable} onClick={() => cmd.step(-1)}>−</button>
            <div className={`dial ${zone.mode}`}>
              <span className="small muted">Doel</span>
              <span className="big-num" style={{ fontSize: 52 }} aria-live="polite">{temp(cmd.target)}</span>
              <span className={`small ${t.stale ? '' : 'muted'}`} style={t.stale ? { color: 'var(--warn)' } : undefined}>nu {temp(t.value)}</span>
            </div>
            <button type="button" className="icon-btn lg" aria-label="Doeltemperatuur hoger" disabled={!controllable} onClick={() => cmd.step(1)}>+</button>
          </div>
          <span className="small row" style={{ justifyContent: 'center', gap: 8 }}>
            <QualityBadge quality={t.quality} /> <span className="muted">{measurementLine(zone)}</span>
          </span>
          <ModeButtons zone={zone} onPick={(m) => cmd.send({ mode: m })} />
          <PendingLine pending={cmd.pending} error={cmd.error} />
          {zone.notice && <div className="note warn">{zone.notice}</div>}
          {!zone.online && <div className="note error">De unit reageert niet. De fabrieksafstandsbediening blijft werken.</div>}
          <div className="row" style={{ gap: 8 }}>
            <ActionButton className="btn primary" disabled={!zone.online || cmd.target === null} onClick={() => cmd.send({ target_c: cmd.target ?? undefined, override_minutes: 120 })}>
              Handmatig 2 uur
            </ActionButton>
            <ActionButton className="btn" disabled={!zone.override || resume.isPending} onClick={() => resume.mutate()}>
              Terug naar schema
            </ActionButton>
          </div>
          {resume.error && <ErrorNote error={resume.error} />}
        </section>

        <section aria-labelledby="sch-h" className="card" style={{ padding: 24, gap: 16 }}>
          <div className="section-head">
            <h2 id="sch-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Schema vandaag</h2>
            {zone.next_change && <span className="small muted">Volgende: {time(zone.next_change.at, tz)} · {zone.next_change.label} {temp(zone.next_change.target_c)}</span>}
          </div>
          <ScheduleBar zoneId={zone.id} tz={tz} />
          <dl className="facts">
            <div><dt>Comfortband</dt><dd>± {p.band_c.toLocaleString('nl-NL')}°</dd></div>
            <div><dt>Min. tijd tussen wijzigingen</dt><dd>{Math.round(p.min_interval_s / 60)} min</dd></div>
            <div><dt>Max. setpoint-sprong</dt><dd>{p.max_step_c.toLocaleString('nl-NL', { minimumFractionDigits: 1 })}°</dd></div>
            <div><dt>Max. starts per uur</dt><dd>{p.max_starts_per_hour}</dd></div>
            <div><dt>Profiel</dt><dd>{zone.profile ?? '–'}</dd></div>
            <div><dt>Verouderd na</dt><dd>{Math.round(p.stale_after_s / 60)} min</dd></div>
          </dl>
        </section>
      </div>

      <section aria-labelledby="gesch-h" className="card" style={{ padding: 24 }}>
        <div className="section-head">
          <h2 id="gesch-h" style={{ fontSize: 16, fontFamily: 'var(--font-body)' }}>Laatste 24 uur</h2>
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
      {isAdmin && <ZoneSettings zone={zone} homeId={homeId} />}
    </>
  );
}

export function ClimatePage() {
  const { homeId } = useHome();
  const zones = useQuery({ queryKey: ['zones', homeId], queryFn: () => api.zones(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  const homes = useQuery({ queryKey: ['homes'], queryFn: api.homes });
  const tz = homes.data?.find((h) => h.id === homeId)?.timezone;
  const [selected, setSelected] = useState<string | null>(null);
  if (!homeId || zones.isLoading) return <Loading />;
  if (!zones.data) return <ErrorNote error={zones.error} />;
  const zone = zones.data.find((z) => z.id === selected) ?? zones.data[0];

  return (
    <>
      <header className="page-head">
        <div>
          <span className="eyebrow">Klimaat · zone</span>
          <h1>{zone?.name ?? 'Klimaat'}</h1>
        </div>
        {zones.data.length > 1 && (
          <div className="segmented" role="tablist" aria-label="Zone">
            {zones.data.map((z) => (
              <button key={z.id} type="button" role="tab" aria-selected={z.id === zone?.id} onClick={() => setSelected(z.id)}>{z.name}</button>
            ))}
          </div>
        )}
      </header>
      {zone ? <ZoneDetail key={zone.id} zone={zone} tz={tz} homeId={homeId} /> : <Empty>Nog geen klimaatzones. Koppel eerst de airco-units via Apparaten.</Empty>}
    </>
  );
}
