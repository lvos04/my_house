import type { EnergySummary, Overview, Zone } from '../api/types';

export const zone = (over: Partial<Zone> = {}): Zone => ({
  id: 'z1',
  name: 'Woonkamer',
  room: 'Woonkamer',
  online: true,
  mode: 'heat',
  target_c: 21,
  fan: 'auto',
  temperature: { value: 20.6, unit: '°C', quality: 'measured', at: new Date().toISOString(), stale: false },
  humidity: { value: 54, unit: '%', quality: 'measured', at: new Date().toISOString(), stale: false },
  control: 'schedule',
  profile: 'Comfort',
  override: null,
  capabilities: { modes: ['heat', 'cool', 'auto'], fan: true, swing: false, min_c: 16, max_c: 30, step_c: 0.5 },
  blocked_modes: [{ mode: 'cool', reason: 'de slaapkamer verwarmt en beide units delen één buitenunit.' }],
  next_change: null,
  policy: { band_c: 0.5, min_interval_s: 900, max_step_c: 1, max_starts_per_hour: 3, stale_after_s: 1800 },
  notice: null,
  ...over,
});

export const energy: EnergySummary = {
  period: 'day',
  start: '2026-11-07T23:00:00Z',
  end: '2026-11-08T23:00:00Z',
  total_kwh: 8.4,
  cost: 2.18,
  currency: 'EUR',
  meters: [
    { role: 'grid_import', label: 'Huis totaal (P1)', kwh: 8.4, cost: 2.18, quality: 'measured', note: null },
    { role: 'airco', label: 'Airco (beide units)', kwh: 3.1, cost: null, quality: 'measured', note: null },
    { role: 'boiler', label: 'Boiler', kwh: 2.2, cost: null, quality: 'measured', note: null },
    { role: 'rest', label: 'Overig huis', kwh: 3.1, cost: null, quality: 'derived', note: 'onbekende apparaten' },
  ],
  buckets: [
    { start: '2026-11-07T23:00:00Z', values: { airco: 0.2, boiler: 0.1, rest: 0.1 }, complete: true },
    { start: '2026-11-08T00:00:00Z', values: { airco: 0.1 }, complete: false },
  ],
};

export const overview: Overview = {
  home: { id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true },
  status: [{ key: 'hue', label: 'Hue Bridge', state: 'ok', detail: 'Verbonden' }],
  zones: [zone()],
  lights: [{ id: 'g1', name: 'Keuken', on: true, brightness: 100, scene: 'Helder', reachable: true, lights_on: 2, lights_total: 2 }],
  energy_today: energy,
  power_now: null,
  alerts: [{ id: 'a1', severity: 'warn', title: 'Sensor verouderd', detail: 'batterij 12%', created_at: new Date().toISOString(), acknowledged: false, source: 'zigbee' }],
};
