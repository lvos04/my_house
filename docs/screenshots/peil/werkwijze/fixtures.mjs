// JSON-fixtures voor de Android-schermafdrukken (Paparazzi), uit dezelfde echte stack en testgegevens
// als de web-afdrukken. Gestart door run_shots.sh met PEIL_SCRIPT=fixtures.mjs.
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// Playwright: pad uit PEIL_PLAYWRIGHT (run_shots.sh kiest standaard die van apps/web); zonder die variabele
// het pakket 'playwright' dat Node vanaf deze map vindt.
const { request: pwRequest } = await import(
  process.env.PEIL_PLAYWRIGHT ? pathToFileURL(process.env.PEIL_PLAYWRIGHT).href : 'playwright'
);

const BASE = process.env.E2E_BASE_URL;
const OUT = process.env.PEIL_OUT;
mkdirSync(OUT, { recursive: true });
const seed = (cmd, data) => JSON.parse(execFileSync(process.env.E2E_PYTHON, [process.env.PEIL_SEED, cmd, JSON.stringify(data)], { encoding: 'utf8', env: process.env }).trim().split('\n').at(-1));
const api = await pwRequest.newContext({ baseURL: BASE });
let r = await api.post('/api/v1/auth/setup', { data: { username: 'beheerder', password: 'lang-genoeg-wachtwoord-2026' } });
const st = await api.storageState();
const csrf = decodeURIComponent(st.cookies.find((c) => c.name === 'shc_csrf').value);
const send = async (m, p, d) => { const x = await api.fetch(`/api/v1${p}`, { method: m, data: d, headers: { 'X-CSRF-Token': csrf } }); if (!x.ok()) console.error(m, p, x.status(), await x.text()); return x.ok() && x.status() !== 204 ? x.json() : null; };
const get = async (p) => (await api.get(`/api/v1${p}`)).json();
const home = await send('POST', '/homes', { name: 'Thuis', timezone: 'Europe/Amsterdam' });
await send('PUT', '/system/settings', { lan_url: 'http://192.168.1.20:8088', remote_url: null });
const ids = seed('all', { home_id: home.id });
const zones = await get(`/homes/${home.id}/zones`);
const wk = zones.find((z) => z.name === 'Woonkamer'), sk = zones.find((z) => z.name === 'Slaapkamer');
await send('PATCH', `/zones/${wk.id}`, { unit_device_id: ids.ac1, sensor_device_id: ids.s1 });
await send('PATCH', `/zones/${sk.id}`, { unit_device_id: ids.ac2, sensor_device_id: ids.s2 });
await send('PUT', `/zones/${wk.id}/schedule`, { blocks: [
  { days: [0, 1, 2, 3, 4], start: '06:30', target_c: 21, mode: null, label: 'Dag' },
  { days: [5, 6], start: '08:00', target_c: 21, mode: null, label: 'Dag' },
  { days: [0, 1, 2, 3, 4, 5, 6], start: '22:30', target_c: 18, mode: null, label: 'Nacht' },
] });
await new Promise((res) => setTimeout(res, 1500));
writeFileSync(`${OUT}/overview.json`, JSON.stringify(await get(`/homes/${home.id}/overview`), null, 1));
writeFileSync(`${OUT}/energy-day.json`, JSON.stringify(await get(`/homes/${home.id}/energy/summary?period=day`), null, 1));
writeFileSync(`${OUT}/schedule-woonkamer.json`, JSON.stringify(await get(`/zones/${wk.id}/schedule`), null, 1));
writeFileSync(`${OUT}/meta.json`, JSON.stringify({ home_id: home.id, woonkamer: wk.id, slaapkamer: sk.id, gemaakt: new Date().toISOString() }, null, 1));
console.error('[fixtures] klaar');
