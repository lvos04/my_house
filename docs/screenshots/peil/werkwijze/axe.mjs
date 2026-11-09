// Nulmeting toegankelijkheid "voor" (Peil-migratie): axe-core 4.14 (wcag2a/aa, 21a/aa, 22aa) plus eigen
// metingen (tekst < 14 px, bedienbare doelen < 44 px, opacity op tekst, overloop) op de echte app.
// Gestart door run_shots.sh met PEIL_SCRIPT=axe.mjs; zelfde testgegevens als shots.mjs.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { chromium, request as pwRequest } from '/home/transcode/truenas-v2/.lokaal/ontwerp-tools/node_modules/playwright/index.mjs';

const AXE = readFileSync('/home/transcode/truenas-v2/.lokaal/ontwerptaal/.review-tools/node_modules/axe-core/axe.min.js', 'utf8');
const BASE = process.env.E2E_BASE_URL;
const seed = (cmd, data) => JSON.parse(execFileSync(process.env.E2E_PYTHON, [process.env.PEIL_SEED, cmd, JSON.stringify(data)], { encoding: 'utf8', env: process.env }).trim().split('\n').at(-1));
const api = await pwRequest.newContext({ baseURL: BASE });
await api.post('/api/v1/auth/setup', { data: { username: 'beheerder', password: 'lang-genoeg-wachtwoord-2026' } });
const st = await api.storageState();
const csrf = decodeURIComponent(st.cookies.find((c) => c.name === 'shc_csrf').value);
const send = async (m, p, d) => { const x = await api.fetch(`/api/v1${p}`, { method: m, data: d, headers: { 'X-CSRF-Token': csrf } }); return x.ok() && x.status() !== 204 ? x.json() : null; };
const get = async (p) => (await api.get(`/api/v1${p}`)).json();
const home = await send('POST', '/homes', { name: 'Thuis', timezone: 'Europe/Amsterdam' });
await send('PUT', '/system/settings', { lan_url: 'http://192.168.1.20:8088', remote_url: null });
const ids = seed('all', { home_id: home.id });
const zones = await get(`/homes/${home.id}/zones`);
const wk = zones.find((z) => z.name === 'Woonkamer'), sk = zones.find((z) => z.name === 'Slaapkamer');
await send('PATCH', `/zones/${wk.id}`, { unit_device_id: ids.ac1, sensor_device_id: ids.s1 });
await send('PATCH', `/zones/${sk.id}`, { unit_device_id: ids.ac2, sensor_device_id: ids.s2 });

const routes = [
  ['overzicht', '/'], ['klimaat-bediening', `/klimaat?zone=${wk.id}&tab=bediening`], ['klimaat-uit-conflict', `/klimaat?zone=${sk.id}&tab=bediening`],
  ['klimaat-weekschema', `/klimaat?zone=${wk.id}&tab=schema`], ['klimaat-instellingen', `/klimaat?zone=${wk.id}&tab=instellingen`],
  ['verlichting', '/verlichting'], ['energie', '/energie'], ['optimalisatie', '/optimalisatie'], ['apparaten', '/apparaten'],
  ['systeem', '/systeem'], ['account-telefoons', '/account#telefoons'], ['installatie', '/installatie'], ['help', '/help'],
];
const browser = await chromium.launch();
const result = [];
for (const theme of ['licht', 'donker']) for (const w of [390, 1440]) {
  const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: w, height: 900 }, colorScheme: theme === 'donker' ? 'dark' : 'light', locale: 'nl-NL', timezoneId: 'Europe/Amsterdam', serviceWorkers: 'block', storageState: st });
  for (const [name, path] of routes) {
    const page = await ctx.newPage();
    await page.goto(path);
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(500);
    await page.addScriptTag({ content: AXE });
    const axe = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, voorbeeld: v.nodes[0]?.target?.join(' ') }));
    });
    const own = await page.evaluate(() => {
      const small = new Map();
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walker.nextNode())) {
        if (!n.textContent.trim()) continue;
        const el = n.parentElement;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none' || el.closest('.sr-only')) continue;
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const fs = parseFloat(cs.fontSize);
        if (fs < 14) small.set(fs, (small.get(fs) ?? 0) + 1);
      }
      const targets = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=tab], [tabindex="0"]')]
        .filter((e) => { const r = e.getBoundingClientRect(); return r.width && r.height && getComputedStyle(e).visibility !== 'hidden'; })
        .filter((e) => !(e.tagName === 'A' && e.closest('p, li, dd') && getComputedStyle(e).display === 'inline'));
      const smallT = targets.filter((e) => { const r = e.getBoundingClientRect(); const lab = e.closest('label'); const lr = lab?.getBoundingClientRect(); return (r.height < 44 || r.width < 44) && !(lr && lr.height >= 44 && lr.width >= 44); })
        .map((e) => `${e.tagName.toLowerCase()}${e.className ? '.' + String(e.className).split(' ').join('.') : ''} ${Math.round(e.getBoundingClientRect().width)}×${Math.round(e.getBoundingClientRect().height)}`);
      const opacity = [...document.querySelectorAll('body *')].filter((e) => parseFloat(getComputedStyle(e).opacity) < 1 && e.textContent.trim()).length;
      return { tekst_kleiner_dan_14px: Object.fromEntries([...small].sort((a, b) => a[0] - b[0])), doelen_kleiner_dan_44px: smallT.length, voorbeelden_doelen: [...new Set(smallT)].slice(0, 6), elementen_met_opacity_en_tekst: opacity, overloop_px: document.documentElement.scrollWidth - document.documentElement.clientWidth };
    });
    result.push({ scherm: name, thema: theme, breedte: w, axe, ...own });
    console.error('[axe]', name, theme, w, axe.map((v) => `${v.id}:${v.n}`).join(' ') || 'axe 0', 'doelen<44:', own.doelen_kleiner_dan_44px);
    await page.close();
  }
  await ctx.close();
}
await browser.close();
writeFileSync(process.env.PEIL_OUT + '/' + (process.env.PEIL_AXE_NAAM || 'axe-voor.json'), JSON.stringify({ gemeten: new Date().toISOString(), axe_core: '4.14.0', tags: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'], resultaten: result }, null, 1) + '\n');
