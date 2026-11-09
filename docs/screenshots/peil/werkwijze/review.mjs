// Stap 5 — onafhankelijke ontwerpreview van my_house (web) tegen Peil 1.1.0.
// Gestart door run_shots.sh (PEIL_SCRIPT=review.mjs): echte stack (PostgreSQL + API + vite preview), fictieve gegevens.
// Per scherm × thema × breedte (320/360/390/768/1024/1440): axe-core 4.14 (WCAG 2.0/2.1/2.2 A+AA), contrast in de
// gerenderde DOM (methode van Peil bouw/controle.cjs: opacity, color-mix, arcering), UI-grenzen van bedienbare
// elementen, doelgrootte, overloop, structuur, groen-audit, "alleen kleur"-kandidaten en de Peil-woordenlijst;
// op 390 en 1440 een volledige Tab-doorloop (ring ≥ 3:1, niet bedekt). Afdrukken (volledige pagina) op 360/390/768/1440.
// Uitvoer: <PEIL_OUT>/review.json + <PEIL_OUT>/shots/<scherm>-<thema>-<breedte>.png
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// Playwright en axe-core: paden uit PEIL_PLAYWRIGHT en PEIL_AXE (run_shots.sh kiest standaard die van apps/web);
// zonder die variabelen de pakketten 'playwright' en 'axe-core' die Node vanaf deze map vindt.
const { chromium, request: pwRequest } = await import(
  process.env.PEIL_PLAYWRIGHT ? pathToFileURL(process.env.PEIL_PLAYWRIGHT).href : 'playwright'
);
const AXE_PAD = process.env.PEIL_AXE || createRequire(import.meta.url).resolve('axe-core/axe.min.js');

const AXE = AXE_PAD;
const BASE = process.env.E2E_BASE_URL;
const OUT = process.env.PEIL_OUT;
const SHOTS = `${OUT}/shots`;
mkdirSync(SHOTS, { recursive: true });
const WIDTHS = (process.env.PEIL_WIDTHS || '320,360,390,768,1024,1440').split(',').map(Number);
const SHOT_WIDTHS = new Set([360, 390, 768, 1440]);
const FOCUS_WIDTHS = new Set([390, 1440]);
const THEMES = (process.env.PEIL_THEMAS || 'licht,donker').split(',');
const ALLEEN = process.env.PEIL_ALLEEN ? new Set(process.env.PEIL_ALLEEN.split(',')) : null;
const GEEN_SHOTS = process.env.PEIL_GEEN_SHOTS === '1';
const ADMIN = { username: 'beheerder', password: 'lang-genoeg-wachtwoord-2026' };
const HUB_FICTIEF = 'https://hub.voorbeeld.invalid:8443';
const log = (...a) => console.error('[review]', ...a);
const runs = [];

function seed(cmd, data) {
  const out = execFileSync(process.env.E2E_PYTHON, [process.env.PEIL_SEED, cmd, JSON.stringify(data)], { encoding: 'utf8', env: process.env });
  return JSON.parse(out.trim().split('\n').at(-1));
}

// ───────────────────────────── meting in de pagina (uitbreiding van Peil bouw/controle.cjs IN_PAGE) ─────────────
const IN_PAGE = (thema) => {
  const cvs = document.createElement('canvas'); cvs.width = cvs.height = 1;
  const ctx = cvs.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  const rgba = (s) => { if (cache.has(s)) return cache.get(s); ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = s; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; const v = [d[0], d[1], d[2], d[3] / 255]; cache.set(s, v); return v; };
  const over = (t, b) => { const a = t[3] + b[3] * (1 - t[3]); if (a === 0) return [0, 0, 0, 0]; return [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / a).concat([a]); };
  const mix = (a, b, t) => [0, 1, 2].map((i) => a[i] * (1 - t) + b[i] * t).concat([1]);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  const colorsIn = (img) => (img.match(/(rgba?\([^)]*\)|oklch\([^)]*\)|oklab\([^)]*\)|color\([^)]*\)|#[0-9a-f]{3,8})/gi) || []);
  const chain = (el) => { const c = []; for (let e = el; e && e.nodeType === 1; e = e.parentElement) c.push(e); return c; };
  function paint(el, fg) {
    const ch = chain(el); const alts = [];
    function rec(k, backdrop) {
      const e = ch[k]; const cs = getComputedStyle(e);
      const inside = over(rgba(cs.backgroundColor), backdrop);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') for (const s of colorsIn(cs.backgroundImage)) { const c = rgba(s); if (c[3] > 0.05) alts.push(over(c, inside)); }
      let Bin, Tin; if (k === 0) { Bin = inside; Tin = over(fg, inside); } else [Bin, Tin] = rec(k - 1, inside);
      const o = parseFloat(cs.opacity); if (o < 1) return [mix(backdrop, Bin, o), mix(backdrop, Tin, o)];
      return [Bin, Tin];
    }
    const [B, T] = rec(ch.length - 1, [255, 255, 255, 1]); return { B, T, alts };
  }
  const cumOpacity = (el) => chain(el).reduce((p, e) => p * parseFloat(getComputedStyle(e).opacity), 1);
  const isSrOnly = (el) => { for (const e of chain(el)) { const cs = getComputedStyle(e); if (cs.clip === 'rect(0px, 0px, 0px, 0px)' || (cs.position === 'absolute' && parseFloat(cs.width) <= 1 && parseFloat(cs.height) <= 1 && cs.overflow === 'hidden') || cs.clipPath === 'inset(50%)') return true; } return false; };
  const visible = (el) => { const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) return false; if (getComputedStyle(el).visibility === 'hidden') return false; return !isSrOnly(el); };
  const desc = (el) => { const id = el.id ? '#' + el.id : ''; const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 4).join('.'); return el.tagName.toLowerCase() + id + (cls ? '.' + cls : ''); };
  const txt = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').replace(/\s+/g, ' ').trim();
  const kort = (s, n = 60) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const context = (el) => { for (let e = el; e && e !== document.body; e = e.parentElement) { const t = kort(e.innerText || e.textContent, 90); if (t.length >= 3) return t; } return ''; };

  // 1. tekstcontrast (≥ 4,5:1; groot ≥ 3:1) en Peil-niveau (< 7:1 = onder de Peil-eis voor tekstniveau 1/2)
  const tekst = []; let minFont = 99;
  for (const el of document.querySelectorAll('body *')) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(el.tagName)) continue;
    const t = txt(el); if (!t || !visible(el)) continue;
    const cs = getComputedStyle(el); const fgS = el instanceof SVGElement ? cs.fill : cs.color;
    if (!fgS || fgS === 'none' || fgS.startsWith('url')) continue;
    const fg = rgba(fgS); const { B, T, alts } = paint(el, fg);
    const fs = parseFloat(cs.fontSize), fw = parseInt(cs.fontWeight);
    const groot = fs >= 24 || (fs >= 18.66 && fw >= 700); const eis = groot ? 3 : 4.5;
    const r = ratio(T, B); let rAlt = r; for (const a of alts) rAlt = Math.min(rAlt, ratio(over(fg, a), a));
    if (fs < minFont) minFont = fs;
    tekst.push({ el: desc(el), t: kort(t), fs, fw, groot, eis, r: +r.toFixed(2), rAlt: +rAlt.toFixed(2), fg: hex(T), bg: hex(B), op: +cumOpacity(el).toFixed(2), dis: !!el.closest(':disabled,[aria-disabled="true"]') });
  }
  for (const el of document.querySelectorAll('input[placeholder],textarea[placeholder]')) { if (!visible(el) || !el.placeholder) continue; const fg = rgba(getComputedStyle(el, '::placeholder').color); const { B, T } = paint(el, fg); tekst.push({ el: desc(el) + '::placeholder', t: kort(el.placeholder, 50), fs: parseFloat(getComputedStyle(el).fontSize), eis: 4.5, r: +ratio(T, B).toFixed(2), rAlt: +ratio(T, B).toFixed(2), fg: hex(T), bg: hex(B), op: 1 }); }
  // tekst in invoervelden en selects (waarde zelf)
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]),select,textarea')) { if (!visible(el)) continue; const v = el.value || (el.tagName === 'SELECT' ? el.selectedOptions[0]?.textContent : ''); if (!v) continue; const fg = rgba(getComputedStyle(el).color); const { B, T } = paint(el, fg); const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < minFont) minFont = fs; tekst.push({ el: desc(el) + '[waarde]', t: kort(v, 40), fs, eis: 4.5, r: +ratio(T, B).toFixed(2), rAlt: +ratio(T, B).toFixed(2), fg: hex(T), bg: hex(B), op: +cumOpacity(el).toFixed(2), dis: el.disabled }); }

  // 2. UI-grenzen (WCAG 1.4.11): veldranden en de grens van knoppen/tabs/tegels ten opzichte van hun omgeving
  const velden = [];
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea')) {
    if (!visible(el)) continue; const cs = getComputedStyle(el); const par = paint(el.parentElement, [0, 0, 0, 0]).B; const own = paint(el, [0, 0, 0, 0]).B;
    if (el.type === 'range') { velden.push({ el: desc(el), type: 'range', opmerking: 'native schuifregelaar (accent-color)', accent: cs.accentColor }); continue; }
    const bc = over(rgba(cs.borderBottomColor), own);
    velden.push({ el: desc(el), type: el.type || el.tagName.toLowerCase(), rand: +ratio(bc, par).toFixed(2), vlak: +ratio(own, par).toFixed(2), bw: parseFloat(cs.borderBottomWidth), kleur: hex(bc), dis: el.disabled });
  }
  const knoppen = [];
  for (const el of document.querySelectorAll('button, [role=button], [role=tab], a.btn, a.nav-link, a.bottom-link, summary, label.choice-card, .light-tile')) {
    if (!visible(el)) continue; const cs = getComputedStyle(el);
    const par = paint(el.parentElement, [0, 0, 0, 0]).B; const own = paint(el, [0, 0, 0, 0]).B;
    let rand = 0, randKleur = '', randStijl = '';
    for (const s of ['Top', 'Right', 'Bottom', 'Left']) {
      const w = parseFloat(cs[`border${s}Width`]); const st = cs[`border${s}Style`]; const c = rgba(cs[`border${s}Color`]);
      if (w > 0 && st !== 'none' && st !== 'hidden' && c[3] > 0) { const bc = over(c, par); const r = ratio(bc, par); if (r > rand) { rand = r; randKleur = hex(bc); randStijl = `${w}px ${st}`; } }
    }
    // inset box-shadow (selectiebalk van nav/tabs) telt als grens
    if (cs.boxShadow && cs.boxShadow !== 'none' && cs.boxShadow.includes('inset')) for (const c of colorsIn(cs.boxShadow)) { const bc = over(rgba(c), own); const rr = ratio(bc, own); if (rgba(c)[3] > 0 && rr > rand) { rand = rr; randKleur = hex(bc); randStijl = 'inset box-shadow'; } }
    const vlak = ratio(own, par);
    const grens = Math.max(rand, vlak);
    const heeftGrens = rand > 0 || vlak > 1.05;
    knoppen.push({ el: desc(el), t: kort(el.innerText || el.getAttribute('aria-label'), 40), grens: +grens.toFixed(2), rand: +rand.toFixed(2), vlak: +vlak.toFixed(2), randKleur, randStijl, vlakKleur: hex(own), omgeving: hex(par), heeftGrens, gekozen: el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-current') === 'page' || el.classList.contains('active'), dis: el.disabled || el.getAttribute('aria-disabled') === 'true' });
  }
  const stippel = [];
  for (const el of document.querySelectorAll('body *')) { const cs = getComputedStyle(el); const sides = ['Top', 'Left', 'Bottom'].filter((s) => ['dashed', 'dotted'].includes(cs['border' + s + 'Style'])); if (!sides.length || !visible(el)) continue; const side = sides[0]; const par = paint(el.parentElement, [0, 0, 0, 0]).B; const bc = over(rgba(cs['border' + side + 'Color']), par); stippel.push({ el: desc(el), t: kort(el.textContent, 40), bw: parseFloat(cs['border' + side + 'Width']), buiten: +ratio(bc, par).toFixed(2), kleur: hex(bc) }); }

  // 3. doelen ≥ 44 px (inline links in lopende tekst en label-rijen van radio/checkbox uitgezonderd)
  const doelen = [];
  for (const el of document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab],[role=link],[tabindex]:not([tabindex="-1"])')) {
    if (!visible(el)) continue; const r = el.getBoundingClientRect();
    const inline = el.tagName === 'A' && getComputedStyle(el).display === 'inline' && el.parentElement && txt(el.parentElement).length > 0;
    if (inline) continue;
    const lab = (el.type === 'radio' || el.type === 'checkbox') && el.closest('label'); const lr = lab && lab.getBoundingClientRect();
    if (lr && lr.width >= 44 && lr.height >= 44) continue;
    if (r.width < 43.5 || r.height < 43.5) doelen.push({ el: desc(el), t: kort(el.innerText || el.getAttribute('aria-label'), 40), w: Math.round(r.width), h: Math.round(r.height) });
  }
  const se = document.scrollingElement; const overloop = se.scrollWidth - se.clientWidth;
  const afgekapt = [];
  for (const el of document.querySelectorAll('body *')) { const cs = getComputedStyle(el); if (cs.textOverflow === 'ellipsis' && visible(el) && el.scrollWidth > el.clientWidth + 1) afgekapt.push({ el: desc(el), t: kort(el.textContent), title: el.getAttribute('title') || '' }); }
  const opacityTekst = [...document.querySelectorAll('body *')].filter((e) => parseFloat(getComputedStyle(e).opacity) < 1 && visible(e) && kort(e.innerText).length > 0).map((e) => `${desc(e)} (${getComputedStyle(e).opacity}) "${kort(e.innerText, 30)}"`);

  // 4. groen-audit: elk element (en ::before) in een gezondheidsgroen of het aqua van slot 3/boiler
  const GROEN = thema === 'donker' ? { '#6fd394': 'healthy', '#0f2b1b': 'healthy-bg', '#a6ebbf': 'healthy-ink', '#199e70': 'chart-3/boiler (aqua)' } : { '#0f6b33': 'healthy', '#dff2e5': 'healthy-bg', '#0a4a22': 'healthy-ink', '#00a672': 'chart-3/boiler (aqua)' };
  const STATUS = thema === 'donker'
    ? { '#6fd394': 'healthy', '#f7c26a': 'degraded', '#ff9b9b': 'down', '#a3adbb': 'unknown', '#8db6ff': 'info/primary', '#0f2b1b': 'healthy-bg', '#33230a': 'degraded-bg', '#3a1215': 'down-bg', '#222932': 'unknown-bg' }
    : { '#0f6b33': 'healthy', '#8f4b00': 'degraded', '#b3141b': 'down', '#566070': 'unknown', '#17469a': 'info', '#dff2e5': 'healthy-bg', '#fcefd6': 'degraded-bg', '#fbe4e4': 'down-bg', '#e9edf2': 'unknown-bg' };
  const groen = []; const kleurAlleen = [];
  const props = (cs, svg) => {
    const p = [['color', cs.color], ['background', cs.backgroundColor]];
    for (const s of ['Top', 'Left', 'Bottom', 'Right']) if (parseFloat(cs[`border${s}Width`]) > 0 && cs[`border${s}Style`] !== 'none') p.push([`border-${s.toLowerCase()}`, cs[`border${s}Color`]]);
    if (svg) { if (cs.fill && cs.fill !== 'none') p.push(['fill', cs.fill]); if (cs.stroke && cs.stroke !== 'none') p.push(['stroke', cs.stroke]); }
    if (cs.boxShadow && cs.boxShadow !== 'none') for (const c of colorsIn(cs.boxShadow)) p.push(['box-shadow', c]);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') for (const c of colorsIn(cs.backgroundImage)) p.push(['background-image', c]);
    return p;
  };
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const svg = el instanceof SVGElement;
    // tekstkleur telt alleen als het element zelf tekst heeft (anders erft alles de kleur)
    const eigen = txt(el).length > 0;
    for (const pseudo of [null, '::before', '::after']) {
      const cs = getComputedStyle(el, pseudo);
      if (pseudo && (cs.content === 'none' || cs.content === 'normal')) continue;
      for (const [prop, val] of props(cs, svg)) {
        if (prop === 'color' && !eigen && !svg && !pseudo) continue;
        const h = hex(rgba(val)); if (rgba(val)[3] < 0.05) continue;
        if (GROEN[h]) groen.push({ el: desc(el) + (pseudo || ''), prop, token: GROEN[h], context: context(el) });
      }
    }
    // kleur-alleen-kandidaten: klein element zonder eigen tekst/naam met een statuskleur als vlak of rand
    const r = el.getBoundingClientRect();
    if (r.width <= 24 && r.height <= 24 && !kort(el.textContent) && !el.getAttribute('aria-label') && !el.closest('[aria-label]')) {
      const cs = getComputedStyle(el);
      const kl = [cs.backgroundColor, svg ? cs.fill : '', svg ? cs.stroke : '', cs.borderTopColor].filter(Boolean).map((v) => hex(rgba(v))).find((h) => STATUS[h] && !h.startsWith('#8db6ff') && STATUS[h] !== 'info');
      if (kl) kleurAlleen.push({ el: desc(el), token: STATUS[kl], context: context(el.parentElement) });
    }
  }

  // 4b. inhoud die buiten zijn eigen knop/tab/tegel/pil steekt (sr-only telt niet)
  const inhoudUit = [];
  for (const el of document.querySelectorAll('button, a.btn, [role=tab], .light-tile, .pill, .bottom-link, .nav-link')) {
    const r = el.getBoundingClientRect(); if (!r.width || el.closest('.sr-only') || !visible(el)) continue;
    let uit = el.scrollWidth - el.clientWidth;
    for (const k of el.querySelectorAll('*')) { if (isSrOnly(k)) continue; const kr = k.getBoundingClientRect(); if (kr.width) uit = Math.max(uit, kr.right - r.right, r.left - kr.left); }
    if (uit > 1) inhoudUit.push(`${desc(el)} "${kort(el.textContent, 30)}" ${Math.round(uit)} px`);
  }
  // 5. zichtbare tekst voor de woordenlijst (alleen wat op het scherm staat)
  const zichtbareTekst = document.body.innerText;
  return { tekst, minFont, velden, knoppen, stippel, doelen, overloop, afgekapt, opacityTekst, groen, kleurAlleen, zichtbareTekst, inhoudUit };
};

const STRUCTUUR = () => {
  const h = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter((e) => e.getClientRects().length).map((e) => +e.tagName[1]);
  const sprong = []; for (let k = 1; k < h.length; k++) if (h[k] > h[k - 1] + 1) sprong.push(`h${h[k - 1]}→h${h[k]}`);
  const ids = {}; for (const e of document.querySelectorAll('[id]')) ids[e.id] = (ids[e.id] || 0) + 1;
  const dubbel = Object.keys(ids).filter((k) => ids[k] > 1);
  const kapot = []; for (const e of document.querySelectorAll('[aria-controls],[aria-describedby],[aria-labelledby]')) for (const a of ['aria-controls', 'aria-describedby', 'aria-labelledby']) for (const id of (e.getAttribute(a) || '').split(/\s+/).filter(Boolean)) if (!document.getElementById(id)) kapot.push(`${a}=${id}`);
  return { h1: document.querySelectorAll('h1').length, sprong, dubbel, kapot, main: document.querySelectorAll('main').length, status: document.querySelectorAll('[role=status]').length, titel: document.title };
};

const FOCUS = () => {
  const el = document.activeElement; if (!el || el === document.body) return null;
  const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
  const desc = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '') + ' "' + ((el.innerText || el.getAttribute('aria-label') || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 30)) + '"';
  const pts = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 2, r.top + 2], [r.right - 2, r.bottom - 2], [r.left + 2, r.bottom - 2], [r.right - 2, r.top + 2]];
  let zichtbaarPt = 0, inViewport = 0;
  for (const [x, y] of pts) { if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue; inViewport++; const top = document.elementFromPoint(x, y); if (top && (top === el || el.contains(top) || top.contains(el) || (el.labels && [...el.labels].some((l) => l.contains(top))))) zichtbaarPt++; }
  const cvs = document.createElement('canvas'); cvs.width = cvs.height = 1; const ctx = cvs.getContext('2d');
  const rgba = (s) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = s; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (t, b) => { const a = t[3] + b[3] * (1 - t[3]); return [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / (a || 1)).concat([a]); };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const bgOf = (e) => { const layers = []; for (let p = e; p && p.nodeType === 1; p = p.parentElement) layers.push(rgba(getComputedStyle(p).backgroundColor)); let c = [255, 255, 255, 1]; for (const l of layers.reverse()) c = over(l, c); return c; };
  const parBg = bgOf(el.parentElement); const ownBg = bgOf(el);
  const ow = parseFloat(cs.outlineWidth); const ost = cs.outlineStyle; const oc = rgba(cs.outlineColor);
  const has = ost !== 'none' && ow > 0;
  const off = parseFloat(cs.outlineOffset);
  // Met negatieve offset ligt de ring binnen het element: dan telt het eigen vlak.
  const tegen = off < 0 ? ownBg : parBg;
  return { desc, tag: el.tagName, ow, ost, offset: off, ringR: has ? +ratio(over(oc, tegen), tegen).toFixed(2) : 0, bs: cs.boxShadow !== 'none' ? cs.boxShadow.slice(0, 80) : '', inViewport, zichtbaarPt, rTop: Math.round(r.top), rBottom: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) };
};

// ───────────────────────────── opzet en metingen ─────────────────────────────
const browser = await chromium.launch();
async function newContext(width, theme, storageState) {
  return browser.newContext({ baseURL: BASE, viewport: { width, height: width >= 1024 ? 900 : 800 }, deviceScaleFactor: 1, colorScheme: theme === 'donker' ? 'dark' : 'light', locale: 'nl-NL', timezoneId: 'Europe/Amsterdam', serviceWorkers: 'block', reducedMotion: 'reduce', storageState });
}
async function hubRoute(page) {
  if (process.env.PEIL_GEEN_HUB === '1') return;
  await page.route('**/_shc/config.json', (r) => r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify({ hub_url: HUB_FICTIEF }) }));
}
async function settle(page) {
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await page.waitForFunction(() => document.querySelectorAll('[aria-busy="true"]').length === 0, null, { timeout: 12000 }).catch(() => {});
  await page.waitForFunction(() => ![...document.querySelectorAll('[role=status]')].some((e) => /^(Laden|Controleren)…$/.test(e.textContent.trim())), null, { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(350);
}
async function maskLan(page) {
  return page.evaluate(() => {
    const re = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g, allow = new Set(['192.168.1.20', '127.0.0.1']);
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n = 0, t;
    while ((t = w.nextNode())) { const m = t.textContent.match(re); if (m && m.some((ip) => !allow.has(ip))) { t.parentElement.setAttribute('data-peil-mask', ''); n++; } }
    return n;
  });
}
async function shot(page, name, theme, width, full = true) {
  if (GEEN_SHOTS || !SHOT_WIDTHS.has(width)) return null;
  const file = `${name}-${theme}-${width}.png`; const vp = page.viewportSize();
  if (full) for (let i = 0; i < 3; i++) { const h = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)); if (h === page.viewportSize().height) break; await page.setViewportSize({ width: vp.width, height: Math.max(h, vp.height) }); await page.waitForTimeout(250); }
  const masked = await maskLan(page);
  await page.screenshot({ path: `${SHOTS}/${file}`, animations: 'disabled', caret: 'hide', mask: masked ? [page.locator('[data-peil-mask]')] : [] });
  if (full) await page.setViewportSize(vp);
  return { file, masked };
}
async function focusWalk(page) {
  const focus = [];
  for (let i = 0; i < 220; i++) {
    await page.keyboard.press('Tab');
    let f = await page.evaluate(FOCUS);
    if (f && (!(f.ost !== 'none' && f.ow > 0) || f.ringR < 3 || (f.inViewport > 0 && f.zichtbaarPt === 0))) { await page.waitForTimeout(300); f = await page.evaluate(FOCUS); }
    if (!f) break; focus.push(f);
    if (focus.length > 3 && f.desc === focus[0].desc) break;
  }
  return {
    aantal: focus.length, eerste: focus[0]?.desc, lijst: focus.map((f) => f.desc).slice(0, 120),
    zonderRing: focus.filter((f) => !(f.ost !== 'none' && f.ow > 0) && !f.bs).map((f) => f.desc),
    zwak: focus.filter((f) => f.ow > 0 && f.ringR < 3).map((f) => `${f.desc} ${f.ringR}`),
    bedekt: focus.filter((f) => f.inViewport > 0 && f.zichtbaarPt === 0).map((f) => `${f.desc} top=${f.rTop} bottom=${f.rBottom}`),
    deelsBedekt: focus.filter((f) => f.inViewport > 0 && f.zichtbaarPt > 0 && f.zichtbaarPt < f.inViewport).map((f) => `${f.desc} ${f.zichtbaarPt}/${f.inViewport}`),
    onzichtbaar: focus.filter((f) => f.w === 0 && f.h === 0).map((f) => f.desc),
    ringMin: focus.filter((f) => f.ow > 0).reduce((m, f) => Math.min(m, f.ringR), 99),
    breedtes: [...new Set(focus.map((f) => f.ow + 'px/' + f.offset))],
  };
}
async function measure(page, name, theme, width, { full = true, focus = true, note = '' } = {}) {
  const m = await page.evaluate(IN_PAGE, theme);
  const run = { scherm: name, thema: theme, breedte: width, url: new URL(page.url()).pathname + new URL(page.url()).search + new URL(page.url()).hash, opmerking: note };
  run.structuur = await page.evaluate(STRUCTUUR);
  await page.addScriptTag({ path: AXE });
  run.axe = await page.evaluate(async () => { const r = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } }); return r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, voorbeeld: v.nodes.slice(0, 3).map((x) => x.target.join(' ') + ' :: ' + (x.failureSummary || '').split('\n').slice(1, 2).join('')) })); });
  run.tekstFout = m.tekst.filter((x) => !x.dis && (x.r < 4.5 || x.rAlt < 4.5));
  run.tekstOnderPeil = m.tekst.filter((x) => !x.dis && x.r >= 4.5 && x.r < 7).map((x) => `${x.el} "${x.t}" ${x.r} (${x.fg} op ${x.bg})`);
  run.tekstUitgeschakeldMin = m.tekst.filter((x) => x.dis).reduce((mn, x) => Math.min(mn, x.r), 99);
  run.minTekst = m.tekst.filter((x) => !x.dis).reduce((mn, x) => Math.min(mn, x.r, x.rAlt), 99);
  run.aantalTekst = m.tekst.length; run.minFont = m.minFont;
  run.veldFout = m.velden.filter((v) => v.rand !== undefined && Math.max(v.rand, v.vlak) < 3);
  run.velden = m.velden.length;
  run.knopGrensFout = m.knoppen.filter((k) => k.heeftGrens && k.grens < 3 && !k.dis);
  run.knopZonderGrens = [...new Set(m.knoppen.filter((k) => !k.heeftGrens).map((k) => `${k.el.split('.').slice(0, 2).join('.')} "${k.t}"`))].slice(0, 30);
  run.knopGrensMin = m.knoppen.filter((k) => k.heeftGrens && !k.dis).reduce((mn, k) => Math.min(mn, k.grens), 99);
  run.stippelFout = m.stippel.filter((s) => s.buiten < 3);
  run.doelen = m.doelen; run.overloop = m.overloop; run.afgekapt = m.afgekapt; run.opacityTekst = m.opacityTekst;
  run.groen = m.groen; run.kleurAlleen = m.kleurAlleen; run.zichtbareTekst = m.zichtbareTekst; run.inhoudUit = m.inhoudUit;
  if (focus && FOCUS_WIDTHS.has(width)) run.focus = await focusWalk(page);
  runs.push(run);
  const kort = [`axe ${run.axe.reduce((s, v) => s + v.n, 0)}`, `tekst<4.5 ${run.tekstFout.length}`, `knopgrens<3 ${run.knopGrensFout.length}`, `veld<3 ${run.veldFout.length}`, `doel<44 ${run.doelen.length}`, `overloop ${run.overloop}`, `inhoud-uit ${run.inhoudUit.length}`, run.focus ? `focus ${run.focus.aantal} zwak ${run.focus.zwak.length} bedekt ${run.focus.bedekt.length}` : ''];
  log(name, theme, width, kort.join(' · '));
  return run;
}
/** Per thema × breedte: context, route, (voorbereiding), meting, afdruk (na de meting, met een verse pagina). */
async function capture(name, path, storageState, { before, note, widths = WIDTHS, full = true } = {}) {
  if (ALLEEN && !ALLEEN.has(name)) return;
  for (const theme of THEMES) for (const w of widths) {
    const ctx = await newContext(w, theme, storageState);
    const page = await ctx.newPage();
    await hubRoute(page);
    await page.goto(path); await settle(page);
    if (before) await before(page, w);
    if (SHOT_WIDTHS.has(w) && !GEEN_SHOTS) await shot(page, name, theme, w, full);
    await measure(page, name, theme, w, { full, note });
    await ctx.close();
  }
}

await capture('eerste-start-beheerder', '/', undefined, { note: 'Lege database: beheerder aanmaken' });
const api = await pwRequest.newContext({ baseURL: BASE });
let r = await api.post('/api/v1/auth/setup', { data: ADMIN });
if (!r.ok()) throw new Error(`setup ${r.status()} ${await r.text()}`);
const adminState = await api.storageState();
const csrf = () => decodeURIComponent(adminState.cookies.find((c) => c.name === 'shc_csrf')?.value ?? '');
async function send(method, path, data) { const res = await api.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': csrf() } }); if (!res.ok()) { log(`LET OP ${method} ${path}: ${res.status()}`); return null; } return res.status() === 204 ? null : res.json(); }
await capture('inloggen', '/', undefined, { note: 'Uitgelogd' });
await capture('woning-aanmaken', '/', adminState, { note: 'Nog geen woning' });

const home = await send('POST', '/homes', { name: 'Thuis', timezone: 'Europe/Amsterdam' });
await send('PUT', '/system/settings', { lan_url: 'http://192.168.1.20:8088', remote_url: null });
const ids = seed('all', { home_id: home.id });
const zones = await (await api.get(`/api/v1/homes/${home.id}/zones`)).json();
const wk = zones.find((z) => z.name === 'Woonkamer'); const sk = zones.find((z) => z.name === 'Slaapkamer');
await send('PATCH', `/zones/${wk.id}`, { unit_device_id: ids.ac1, sensor_device_id: ids.s1 });
await send('PATCH', `/zones/${sk.id}`, { unit_device_id: ids.ac2, sensor_device_id: ids.s2 });
const weekdays = [0, 1, 2, 3, 4], all = [0, 1, 2, 3, 4, 5, 6];
await send('PUT', `/zones/${wk.id}/schedule`, { blocks: [{ days: weekdays, start: '06:30', target_c: 21, mode: null, label: 'Dag' }, { days: [5, 6], start: '08:00', target_c: 21, mode: null, label: 'Dag' }, { days: all, start: '22:30', target_c: 18, mode: null, label: 'Nacht' }] });
await send('POST', '/users', { username: 'meekijker', password: 'kijken-mag-altijd-2026', role: 'viewer' });

const pages = [
  ['overzicht', '/'], ['klimaat-bediening', `/klimaat?zone=${wk.id}&tab=bediening`], ['klimaat-uit-conflict', `/klimaat?zone=${sk.id}&tab=bediening`],
  ['klimaat-weekschema', `/klimaat?zone=${wk.id}&tab=schema`], ['klimaat-profielen', `/klimaat?zone=${wk.id}&tab=profielen`], ['klimaat-instellingen', `/klimaat?zone=${wk.id}&tab=instellingen`],
  ['verlichting', '/verlichting'], ['energie', '/energie'], ['optimalisatie', '/optimalisatie'], ['apparaten', '/apparaten'], ['systeem', '/systeem'],
  ['account-mijn', '/account'], ['account-telefoons', '/account#telefoons'], ['installatie', '/installatie'], ['help', '/help'],
];
for (const [name, path] of pages) await capture(name, path, adminState);
await capture('meer-sheet', '/', adminState, { widths: WIDTHS.filter((w) => w < 1024), full: false, note: 'Meer-sheet open', before: async (page) => { await page.getByRole('navigation', { name: 'Hoofdmenu (mobiel)' }).getByRole('button', { name: 'Meer' }).click(); await page.waitForTimeout(300); } });
await capture('thema-donker-gekozen', '/', adminState, { full: false, note: 'In de app Donker gekozen', before: async (page, w) => { if (w < 1024) await page.getByRole('navigation', { name: 'Hoofdmenu (mobiel)' }).getByRole('button', { name: 'Meer' }).click(); await page.getByRole('group', { name: 'Thema' }).first().getByRole('button', { name: 'Donker' }).click(); await page.waitForTimeout(300); } });
const viewerApi = await pwRequest.newContext({ baseURL: BASE });
r = await viewerApi.post('/api/v1/auth/login', { data: { username: 'meekijker', password: 'kijken-mag-altijd-2026' } });
if (r.ok()) await capture('overzicht-kijker', '/', await viewerApi.storageState(), { note: 'Rol kijker' });

seed('stale', {});
await capture('overzicht-verouderd', '/', adminState, { note: 'Sensorwaarden 3 uur oud' });
await capture('klimaat-verouderd', `/klimaat?zone=${wk.id}&tab=bediening`, adminState, { note: 'Woonkamer met verouderde sensorwaarde' });

// forced colors: blijven de stippelranden en is de focus Highlight? (één scherm, 390, licht)
{
  const ctx = await newContext(390, 'licht', adminState); const page = await ctx.newPage();
  await page.emulateMedia({ forcedColors: 'active' });
  await page.goto('/verlichting').catch(() => {}); await settle(page);
  const fc = await page.evaluate(() => [...document.querySelectorAll('.light-tile.is-unreachable, .light-tile:disabled, .pill.unknown, .q.missing, .offline-banner, .note.unknown')].map((e) => `${e.className}: ${getComputedStyle(e).borderTopStyle}`));
  if (!GEEN_SHOTS) await page.screenshot({ path: `${SHOTS}/forced-colors-verlichting-390.png`, fullPage: true });
  runs.push({ scherm: 'forced-colors', thema: 'licht', breedte: 390, stippel: fc });
  await ctx.close();
}

if (!ALLEEN || ALLEEN.has('overzicht-offline')) {
  const open = [];
  for (const theme of THEMES) for (const w of WIDTHS) {
    const ctx = await newContext(w, theme, adminState); const page = await ctx.newPage(); await hubRoute(page);
    await page.goto(`/klimaat?zone=${wk.id}&tab=bediening`); await settle(page);
    await page.locator('a[href="/"]:visible').first().click(); await settle(page);
    open.push({ ctx, page, theme, w });
  }
  const pid = Number(readFileSync(`${process.env.E2E_STATE_DIR}/api.pid`, 'utf8'));
  process.kill(pid, 'SIGTERM'); log('API gestopt');
  for (const o of open) await o.page.getByRole('status').filter({ hasText: 'Geen verbinding met de server' }).waitFor({ timeout: 60000 });
  for (const o of open) { await o.page.waitForTimeout(200); await shot(o.page, 'overzicht-offline', o.theme, o.w); await measure(o.page, 'overzicht-offline', o.theme, o.w, { note: 'API gestopt' }); }
  for (const o of open) { await o.page.locator('a[href="/klimaat"]:visible').first().click(); await o.page.waitForTimeout(700); await shot(o.page, 'klimaat-offline', o.theme, o.w); await measure(o.page, 'klimaat-offline', o.theme, o.w, { note: 'API gestopt' }); }
  for (const o of open) { await o.page.locator('a[href="/verlichting"]:visible').first().click(); await o.page.waitForTimeout(1500); await shot(o.page, 'verlichting-offline-nooit-geladen', o.theme, o.w); await measure(o.page, 'verlichting-offline-nooit-geladen', o.theme, o.w, { note: 'API gestopt, nooit geladen (1,5 s)' }); }
  await new Promise((r) => setTimeout(r, 10000));
  for (const o of open) { await shot(o.page, 'verlichting-offline-nooit-geladen-later', o.theme, o.w); await measure(o.page, 'verlichting-offline-nooit-geladen-later', o.theme, o.w, { focus: false, note: 'API gestopt, nooit geladen (na de herhaalpogingen)' }); }
  for (const o of open) await o.ctx.close();
  await capture('server-onbereikbaar', '/', undefined, { note: 'API gestopt, nieuwe sessie' });
}

writeFileSync(`${OUT}/review.json`, JSON.stringify({ gemeten: new Date().toISOString(), axe_core: '4.14.0', tags: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'], runs }, null, 1) + '\n');
await browser.close();
log(`klaar: ${runs.length} metingen`);
