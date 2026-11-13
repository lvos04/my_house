// Schermafdrukken "voor"/"na" van de my_house-web-app (Peil-migratie, stap 2). Gestart door run_shots.sh,
// dat de echte stack (PostgreSQL + API + vite preview) op vrije poorten heeft gestart.
// Uitvoer: <PEIL_OUT>/<scherm>-<licht|donker>-<breedte>.png (volledige pagina) + index.json.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { ADMIN, VIEWER } from './testaccounts.mjs';

// Playwright: pad uit PEIL_PLAYWRIGHT (run_shots.sh kiest standaard die van apps/web); zonder die variabele
// het pakket 'playwright' dat Node vanaf deze map vindt.
const { chromium, request: pwRequest } = await import(
  process.env.PEIL_PLAYWRIGHT ? pathToFileURL(process.env.PEIL_PLAYWRIGHT).href : 'playwright'
);

const BASE = process.env.E2E_BASE_URL;
const OUT = process.env.PEIL_OUT;
const WIDTHS = (process.env.PEIL_WIDTHS || '360,390,768,1440').split(',').map(Number);
const THEMES = (process.env.PEIL_THEMAS || 'licht,donker').split(',');
const ALLEEN = process.env.PEIL_ALLEEN ? new Set(process.env.PEIL_ALLEEN.split(',')) : null;
const index = [];
const log = (...a) => console.error('[shots]', ...a);

function seed(cmd, data) {
  const out = execFileSync(process.env.E2E_PYTHON, [process.env.PEIL_SEED, cmd, JSON.stringify(data)], { encoding: 'utf8', env: process.env });
  return JSON.parse(out.trim().split('\n').at(-1));
}

const browser = await chromium.launch();

// "Terug naar de Hub" (O-11): vite preview kent /_shc/config.json niet (nginx maakt het uit HUB_URL). Voor de
// afdrukken een FICTIEF adres (.invalid bestaat nooit), zodat de link zichtbaar is. Geen echt Hub-adres.
const HUB_FICTIEF = 'https://hub.voorbeeld.invalid:8443';
async function hubRoute(page) {
  if (process.env.PEIL_GEEN_HUB === '1') return;
  await page.route('**/_shc/config.json', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ hub_url: HUB_FICTIEF }) }));
}

async function newContext(width, theme, storageState) {
  return browser.newContext({
    baseURL: BASE,
    viewport: { width, height: width >= 1024 ? 900 : 800 },
    deviceScaleFactor: 1,
    colorScheme: theme === 'donker' ? 'dark' : 'light',
    locale: 'nl-NL',
    timezoneId: 'Europe/Amsterdam',
    serviceWorkers: 'block',
    reducedMotion: 'reduce',
    storageState,
  });
}

async function settle(page) {
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await page.waitForFunction(() => ![...document.querySelectorAll('[role=status]')].some((e) => /^(Laden|Controleren)…$/.test(e.textContent.trim())), null, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(400);
}

async function snap(page, name, theme, width, note = '', full = true) {
  const file = `${name}-${theme}-${width}.png`;
  const vp = page.viewportSize();
  if (full) {
    // Viewport = hele pagina, zodat vaste balken (ondernavigatie) onderaan staan en niets bedekken.
    for (let i = 0; i < 3; i++) {
      const h = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
      if (h === page.viewportSize().height) break;
      await page.setViewportSize({ width: vp.width, height: Math.max(h, vp.height) });
      await page.waitForTimeout(250);
    }
  }
  // Echte LAN-adressen van de testmachine (bijv. het antwoord van de netwerkdetectie) worden afgedekt;
  // het fictieve thuisadres 192.168.1.20 en loopback blijven zichtbaar.
  const masked = await page.evaluate(() => {
    const re = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g, allow = new Set(['192.168.1.20', '127.0.0.1']);
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n = 0, t;
    while ((t = w.nextNode())) {
      const m = t.textContent.match(re);
      if (m && m.some((ip) => !allow.has(ip))) { t.parentElement.setAttribute('data-peil-mask', ''); n++; }
    }
    return n;
  });
  await page.screenshot({ path: `${OUT}/${file}`, animations: 'disabled', caret: 'hide', mask: masked ? [page.locator('[data-peil-mask]')] : [] });
  if (masked) note = `${note}${note ? ' · ' : ''}${masked} echt LAN-adres afgedekt (magenta)`;
  const h1 = await page.locator('h1').first().textContent({ timeout: 1000 }).catch(() => null);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  index.push({ file, scherm: name, thema: theme, breedte: width, url: new URL(page.url()).pathname + new URL(page.url()).search + new URL(page.url()).hash, h1: h1?.trim() ?? null, overloop_px: overflow, opmerking: note });
  log(file, overflow > 1 ? `OVERLOOP ${overflow}px` : '');
  if (full) await page.setViewportSize(vp);
}

/** Maakt voor elk thema × breedte een context, opent de route en maakt de afdruk. */
async function capture(name, path, storageState, { before, note, widths = WIDTHS, full = true } = {}) {
  if (ALLEEN && !ALLEEN.has(name)) return;
  for (const theme of THEMES) for (const w of widths) {
    const ctx = await newContext(w, theme, storageState);
    const page = await ctx.newPage();
    await hubRoute(page);
    await page.goto(path);
    await settle(page);
    if (before) await before(page, w);
    await snap(page, name, theme, w, note, full);
    await ctx.close();
  }
}

// ---------------------------------------------------------------- 1. eerste start (lege database)
await capture('eerste-start-beheerder', '/', undefined, { note: 'Lege database: beheerder aanmaken (AuthPage mode=setup)' });

// ---------------------------------------------------------------- 2. beheerder, inloggen, geen woning
const api = await pwRequest.newContext({ baseURL: BASE });
let r = await api.post('/api/v1/auth/setup', { data: ADMIN });
if (!r.ok()) throw new Error(`setup ${r.status()} ${await r.text()}`);
const adminState = await api.storageState();
const csrf = () => decodeURIComponent(adminState.cookies.find((c) => c.name === 'shc_csrf')?.value ?? '');
async function send(method, path, data) {
  const res = await api.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': csrf() } });
  if (!res.ok()) { log(`LET OP ${method} ${path}: ${res.status()} ${await res.text()}`); return null; }
  return res.status() === 204 ? null : res.json();
}
await capture('inloggen', '/', undefined, { note: 'Uitgelogd: inlogscherm (AuthPage mode=login)' });
await capture('woning-aanmaken', '/', adminState, { note: 'Ingelogd, nog geen woning (NoHomeSetup)' });

// ---------------------------------------------------------------- 3. testgegevens
const home = await send('POST', '/homes', { name: 'Thuis', timezone: 'Europe/Amsterdam' });
await send('PUT', '/system/settings', { lan_url: 'http://192.168.1.20:8088', remote_url: null });
const ids = seed('all', { home_id: home.id });
log('seed', JSON.stringify(ids));
const zones = await (await api.get(`/api/v1/homes/${home.id}/zones`)).json();
const wk = zones.find((z) => z.name === 'Woonkamer');
const sk = zones.find((z) => z.name === 'Slaapkamer');
await send('PATCH', `/zones/${wk.id}`, { unit_device_id: ids.ac1, sensor_device_id: ids.s1 });
await send('PATCH', `/zones/${sk.id}`, { unit_device_id: ids.ac2, sensor_device_id: ids.s2 });
const weekdays = [0, 1, 2, 3, 4], all = [0, 1, 2, 3, 4, 5, 6];
await send('PUT', `/zones/${wk.id}/schedule`, { blocks: [
  { days: weekdays, start: '06:30', target_c: 21, mode: null, label: 'Dag' },
  { days: [5, 6], start: '08:00', target_c: 21, mode: null, label: 'Dag' },
  { days: all, start: '22:30', target_c: 18, mode: null, label: 'Nacht' },
] });
await send('POST', '/users', { ...VIEWER, role: 'viewer' });

// ---------------------------------------------------------------- 4. alle pagina's
const pages = [
  ['overzicht', '/', 'Overzicht met zones, licht, energie vandaag en meldingen'],
  ['klimaat-bediening', `/klimaat?zone=${wk.id}&tab=bediening`, 'Woonkamer verwarmt (airco aan, sensor vers)'],
  ['klimaat-uit-conflict', `/klimaat?zone=${sk.id}&tab=bediening`, 'Slaapkamer: airco uit; koelen geblokkeerd door gedeelde buitenunit'],
  ['klimaat-weekschema', `/klimaat?zone=${wk.id}&tab=schema`, 'Weekschema-editor'],
  ['klimaat-profielen', `/klimaat?zone=${wk.id}&tab=profielen`, 'Comfortprofielen'],
  ['klimaat-instellingen', `/klimaat?zone=${wk.id}&tab=instellingen`, 'Zone-koppeling en regelgrenzen'],
  ['verlichting', '/verlichting', 'Groepen (aan, uit, onbereikbaar), scènes, knoppen'],
  ['energie', '/energie', 'Energie per dag: meters, staafgrafiek, tabel, kosten'],
  ['optimalisatie', '/optimalisatie', 'Adaptief model, weer, experimenten'],
  ['apparaten', '/apparaten', 'Apparaten per categorie, rollen, compatibiliteit'],
  ['systeem', '/systeem', 'Status, versie, adressen, back-ups, meldingen, herstellen, woningen'],
  ['account-mijn', '/account', 'Account: eigen wachtwoord'],
  ['account-gebruikers', '/account#gebruikers', 'Account: gebruikers'],
  ['account-telefoons', '/account#telefoons', 'Account: telefoons koppelen (QR)'],
  ['account-logboek', '/account#logboek', 'Account: logboek'],
  ['installatie', '/installatie', 'Installatiewizard stap 1 (controles vooraf)'],
  ['help', '/help', 'Help per fabrikant'],
];
for (const [name, path, note] of pages) await capture(name, path, adminState, { note });

// Meer-sheet (alleen < 1024 px, daarboven is er een zijbalk)
await capture('meer-sheet', '/', adminState, {
  widths: WIDTHS.filter((w) => w < 1024),
  note: 'Mobiel: Meer-sheet open (alleen het venster)',
  full: false,
  before: async (page) => { await page.getByRole('button', { name: 'Meer' }).click(); await page.waitForTimeout(300); },
});
// Themaknop (O-09): systeem licht, gekozen Donker (de knop in de stand Donker).
await capture('thema-donker-gekozen', '/', adminState, {
  note: 'Systeem licht, in de app Donker gekozen (themaknop)',
  full: false,
  before: async (page, w) => {
    if (w < 1024) await page.getByRole('button', { name: 'Meer' }).click();
    await page.getByRole('group', { name: 'Thema' }).getByRole('button', { name: 'Donker' }).click();
    await page.waitForTimeout(300);
  },
});
// Kijker (alleen lezen)
const viewerApi = await pwRequest.newContext({ baseURL: BASE });
r = await viewerApi.post('/api/v1/auth/login', { data: VIEWER });
const viewerState = r.ok() ? await viewerApi.storageState() : null;
if (viewerState) await capture('overzicht-kijker', '/', viewerState, { note: 'Rol kijker: geen bediening' });

// ---------------------------------------------------------------- 5. verouderde sensorwaarden
seed('stale', {});
await capture('overzicht-verouderd', '/', adminState, { note: 'Sensorwaarden en vermogen 3 uur oud (verouderd)' });
await capture('klimaat-verouderd', `/klimaat?zone=${wk.id}&tab=bediening`, adminState, { note: 'Woonkamer met verouderde sensorwaarde' });

// ---------------------------------------------------------------- 6. offline (API gestopt)
if (!ALLEEN || ALLEEN.has('overzicht-offline')) {
const open = [];
for (const theme of THEMES) for (const w of WIDTHS) {
  const ctx = await newContext(w, theme, adminState);
  const page = await ctx.newPage();
  await hubRoute(page);
  await page.goto(`/klimaat?zone=${wk.id}&tab=bediening`);
  await settle(page);
  await page.locator('a[href="/"]:visible').first().click();
  await settle(page);
  open.push({ ctx, page, theme, w });
}
const pid = Number(readFileSync(`${process.env.E2E_STATE_DIR}/api.pid`, 'utf8'));
process.kill(pid, 'SIGTERM');
log('API gestopt; wachten op de offline-banner');
for (const o of open) {
  await o.page.getByRole('status').filter({ hasText: 'Geen verbinding met de server' }).waitFor({ timeout: 60000 });
}
for (const o of open) { await o.page.waitForTimeout(200); await snap(o.page, 'overzicht-offline', o.theme, o.w, 'API gestopt: banner, laatst bekende gegevens gedimd, bediening uit'); }
for (const o of open) {
  await o.page.locator('a[href="/klimaat"]:visible').first().click();
  await o.page.waitForTimeout(600);
  await snap(o.page, 'klimaat-offline', o.theme, o.w, 'API gestopt: klimaat uit de cache, bediening uit');
}
for (const o of open) {
  await o.page.locator('a[href="/verlichting"]:visible').first().click();
  await o.page.waitForTimeout(1500);
  await snap(o.page, 'verlichting-offline-nooit-geladen', o.theme, o.w, 'API gestopt: pagina die nog nooit geladen was blijft op "Laden…" staan');
}
for (const o of open) await o.ctx.close();
}
await capture('server-onbereikbaar', '/', undefined, { note: 'API gestopt, nieuwe sessie: "De server is niet bereikbaar"' });

writeFileSync(`${OUT}/index.json`, JSON.stringify(index, null, 1) + '\n');
await browser.close();
log(`klaar: ${index.length} afdrukken`);
