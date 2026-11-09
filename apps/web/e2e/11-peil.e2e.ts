// Flow 11: ontwerptaal Peil (docs/ontwerp/PEIL_MIGRATIE.md §12, PEIL_REVIEW.md). Toegankelijkheid met axe-core op de
// hoofdschermen (WCAG 2.0/2.1/2.2 A en AA) in licht en donker op 390 en 1440 px, de Peil-testmatrix
// (geen zijwaartse scroll, geen tekst < 14 px, bedienbare doelen ≥ 44 × 44 px, inhoud binnen zijn knop of tegel),
// een volledige Tab-doorloop (ring ≥ 3:1, niet achter een vaste balk) en de twee toegestane
// toevoegingen: de themaknop Systeem / Licht / Donker (O-09) en de link "Terug naar de Hub" (O-11).
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { expect, test } from './helpers';

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const SCREENS: [string, string][] = [
  ['Overzicht', '/'],
  ['Klimaat', '/klimaat'],
  ['Klimaat › Weekschema', '/klimaat?tab=schema'],
  ['Verlichting', '/verlichting'],
  ['Energie', '/energie'],
  ['Optimalisatie', '/optimalisatie'],
  ['Apparaten', '/apparaten'],
  ['Systeem', '/systeem'],
  ['Account', '/account'],
  ['Installatie', '/installatie'],
  ['Help', '/help'],
];

/** Wacht tot de pagina klaar is met laden (één h1, geen "Laden…"). */
async function settled(page: Page) {
  await expect(page.locator('main h1, .auth h1').first()).toBeVisible();
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 15_000 });
}

async function axe(page: Page, what: string) {
  const result = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const found = result.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).slice(0, 5).join(' | ')}`);
  expect(found, `axe ${what}`).toEqual([]);
}

/** Tekst kleiner dan 14 px (alleen zichtbare elementen met eigen tekst; sr-only telt niet mee). */
async function smallText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const own = Array.from(el.childNodes).some((n) => n.nodeType === Node.TEXT_NODE && n.textContent!.trim());
      if (!own) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 1 || r.height <= 1) continue; // sr-only en verborgen
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      if (parseFloat(cs.fontSize) < 13.95) out.push(`${el.tagName.toLowerCase()}.${el.className}: ${cs.fontSize} "${el.textContent!.trim().slice(0, 30)}"`);
    }
    return out;
  });
}

/** Bedienbare doelen kleiner dan 44 × 44 px. Uitzondering (WCAG 2.5.8): links in lopende tekst. Een
 * checkbox of radio telt met zijn label-rij. */
async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const sel = 'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="tab"], [role="button"]';
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      let target: HTMLElement = el;
      if (el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio')) target = el.closest('label') ?? el;
      const r = target.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue; // niet zichtbaar
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden') continue;
      if (el.closest('.sr-only, .skip-link')) continue;
      if (el.tagName === 'A' && cs.display === 'inline') {
        const p = el.parentElement;
        const inText = p && Array.from(p.childNodes).some((n) => n !== el && n.nodeType === Node.TEXT_NODE && n.textContent!.trim());
        if (inText) continue;
      }
      if (r.width < 43.5 || r.height < 43.5) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30)}": ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
    return out;
  });
}

/** Bedienbare elementen of blokken die rechts buiten hun kaart steken (geen zijwaartse scroll, maar wel
 * kapot). Tabellen in .table-wrap mogen scrollen en tellen niet mee. */
async function outsideCards(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const card of Array.from(document.querySelectorAll<HTMLElement>('main .card'))) {
      const c = card.getBoundingClientRect();
      if (!c.width) continue;
      for (const el of Array.from(card.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], .sched-block, .card'))) {
        if (el.closest('.table-wrap, [role="img"], .sr-only')) continue;
        const r = el.getBoundingClientRect();
        if (r.width && r.right > c.right + 1) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30)}" tot ${Math.round(r.right)} (kaart tot ${Math.round(c.right)})`);
      }
    }
    return out;
  });
}

/** Inhoud die buiten zijn eigen knop, tab, tegel of pil steekt (review stap 5: "✓ Verwarmen" stak 10 px uit de
 * modusknop, "Woonkamer" 18 px uit de lichttegel op 320 px). sr-only tekst telt niet. De ondernavigatie is
 * uitgezonderd: vijf kolommen van 64 px op 320 px zijn te smal voor een vet "Overzicht" (PEIL_REVIEW.md, open punt). */
async function contentOutside(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('main button, main a.btn, main [role="tab"], main .light-tile, main .pill, [role="dialog"] button'))) {
      const r = el.getBoundingClientRect();
      if (!r.width || el.closest('.sr-only')) continue;
      let uit = el.scrollWidth - el.clientWidth;
      for (const k of Array.from(el.querySelectorAll<HTMLElement>('*'))) {
        if (k.closest('.sr-only')) continue;
        const kr = k.getBoundingClientRect();
        if (kr.width) uit = Math.max(uit, kr.right - r.right, r.left - kr.left);
      }
      if (uit > 1) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || '').trim().slice(0, 30)}" steekt ${Math.round(uit)} px uit`);
    }
    return out;
  });
}

/** De focus van de huidige stop: ring aanwezig (≥ 2 px), contrast van de ring met het vlak eromheen, en of het
 * element helemaal achter een vaste balk ligt (WCAG 2.4.7, 2.4.11, Peil §12). */
async function focusStop(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const naam = `${el.tagName.toLowerCase()} "${(el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 30)}"`;
    // Het klok-/kalenderknopje in een tijd- of datumveld tekent zijn eigen ring (browser); dan matcht :focus niet.
    if (!el.matches(':focus')) return { naam, browserdeel: true, ring: 0, contrast: 0, bedekt: false };
    const cs = getComputedStyle(el);
    const c = document.createElement('canvas').getContext('2d')!;
    const rgb = (s: string) => { c.clearRect(0, 0, 1, 1); c.fillStyle = '#000'; c.fillStyle = s; c.fillRect(0, 0, 1, 1); return Array.from(c.getImageData(0, 0, 1, 1).data); };
    const lum = (v: number[]) => { const f = (x: number) => { x /= 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * f(v[0]) + 0.7152 * f(v[1]) + 0.0722 * f(v[2]); };
    let bg = [255, 255, 255, 255];
    for (let p = el.parentElement; p; p = p.parentElement) { const b = rgb(getComputedStyle(p).backgroundColor); if (b[3] === 255) { bg = b; break; } }
    const ring = cs.outlineStyle === 'none' ? 0 : parseFloat(cs.outlineWidth);
    const a = lum(rgb(cs.outlineColor)), b = lum(bg);
    const contrast = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const r = el.getBoundingClientRect();
    const pts = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 6, r.top + 6], [r.right - 6, r.bottom - 6], [r.left + 6, r.bottom - 6], [r.right - 6, r.top + 6]]
      .filter(([x, y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
    const zichtbaar = pts.filter(([x, y]) => { const t = document.elementFromPoint(x, y); return !!t && (t === el || el.contains(t) || t.contains(el)); }).length;
    return { naam, browserdeel: false, ring, contrast, bedekt: pts.length > 0 && zichtbaar === 0 };
  });
}

/** Het element dat het verst buiten de pagina steekt (diagnose bij zijwaartse scroll). */
async function widest(page: Page): Promise<string> {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    let worst: { el: Element; right: number } | null = null;
    for (const el of Array.from(document.querySelectorAll('body *'))) {
      const r = el.getBoundingClientRect();
      if (r.width && r.right > vw + 1 && (!worst || r.right > worst.right)) worst = { el, right: r.right };
    }
    if (!worst) return 'onbekend';
    const e = worst.el as HTMLElement;
    return `${e.tagName.toLowerCase()}.${String(e.className).split(' ').join('.')} "${(e.textContent ?? '').trim().slice(0, 40)}" tot ${Math.round(worst.right)} px`;
  });
}

for (const [scheme, thema] of [['light', 'licht'], ['dark', 'donker']] as const) {
  for (const width of [390, 1440]) {
    test(`axe 0 op de hoofdschermen · ${thema} · ${width} px`, async ({ adminPage: page }) => {
      test.setTimeout(240_000);
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width, height: width >= 1024 ? 900 : 800 });
      for (const [name, path] of SCREENS) {
        await page.goto(path);
        await settled(page);
        // Thema "Systeem" volgt het apparaat: het opgeloste thema staat altijd op <html>.
        await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
        await axe(page, `${name} (${thema}, ${width} px)`);
      }
      // De Meer-sheet (alleen onder 1024 px).
      if (width < 1024) {
        await page.goto('/');
        await settled(page);
        await page.getByRole('navigation', { name: 'Hoofdmenu (mobiel)' }).getByRole('button', { name: 'Meer' }).click();
        await expect(page.getByRole('dialog', { name: 'Meer' })).toBeVisible();
        await axe(page, `Meer-sheet (${thema}, ${width} px)`);
      }
    });
  }

  test(`axe 0 op inloggen · ${thema}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Inloggen' })).toBeVisible();
      await axe(page, `Inloggen (${thema}, ${width} px)`);
    }
  });
}

test('Peil-matrix: geen zijwaartse scroll, geen tekst < 14 px, doelen ≥ 44 px, inhoud binnen het element (320–1440 px)', async ({ adminPage: page }) => {
  test.setTimeout(300_000);
  const fouten: string[] = [];
  for (const width of [320, 360, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    for (const [name, path] of SCREENS) {
      await page.goto(path);
      await settled(page);
      const overloop = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overloop > 1) fouten.push(`${width} px ${name}: ${overloop} px zijwaartse scroll (${await widest(page)})`);
      for (const f of await outsideCards(page)) fouten.push(`${width} px ${name}: buiten de kaart ${f}`);
      for (const f of await smallText(page)) fouten.push(`${width} px ${name}: tekst ${f}`);
      for (const f of await smallTargets(page)) fouten.push(`${width} px ${name}: doel ${f}`);
      for (const f of await contentOutside(page)) fouten.push(`${width} px ${name}: inhoud ${f}`);
    }
  }
  expect(fouten).toEqual([]);
});

for (const width of [390, 1440]) {
  test(`Tab-doorloop: elke stop heeft een ring ≥ 3:1 en ligt niet achter een vaste balk · ${width} px`, async ({ adminPage: page }) => {
    test.setTimeout(300_000);
    const fouten: string[] = [];
    for (const [scheme] of [['light'], ['dark']] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width, height: width >= 1024 ? 900 : 800 });
      for (const [name, path] of SCREENS) {
        await page.goto(path);
        await settled(page);
        const gezien: string[] = [];
        for (let i = 0; i < 200; i++) {
          await page.keyboard.press('Tab');
          const f = await focusStop(page);
          if (!f) break;
          if (gezien.length > 3 && f.naam === gezien[0]) break;
          gezien.push(f.naam);
          if (f.browserdeel) continue;
          if (f.ring < 2) fouten.push(`${scheme} ${name}: ${f.naam} zonder focusring`);
          else if (f.contrast < 3) fouten.push(`${scheme} ${name}: ${f.naam} ring ${f.contrast.toFixed(2)}:1`);
          if (f.bedekt) fouten.push(`${scheme} ${name}: ${f.naam} ligt achter een vaste balk`);
        }
        expect(gezien.length, `${name}: Tab bereikt iets`).toBeGreaterThan(0);
      }
    }
    expect(fouten).toEqual([]);
  });
}

test('themaknop: Donker blijft na herladen, Systeem volgt het apparaat (O-09)', async ({ adminPage: page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await settled(page);
  const html = page.locator('html');
  await expect(html).toHaveAttribute('data-theme', 'light');
  const thema = page.getByRole('navigation', { name: 'Hoofdmenu' }).getByRole('group', { name: 'Thema' });
  await expect(thema.getByRole('button', { name: 'Systeem' })).toHaveAttribute('aria-pressed', 'true');

  await thema.getByRole('button', { name: 'Donker' }).click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(html).toHaveClass(/\bdark\b/);
  await expect(thema.getByRole('button', { name: 'Donker' })).toBeFocused();
  const achtergrond = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(achtergrond).toBe('rgb(10, 13, 18)'); // Peil background donker #0A0D12

  await page.reload();
  await settled(page);
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('meta[name="theme-color"]').first()).toHaveAttribute('content', '#0A0D12');

  await thema.getByRole('button', { name: 'Licht' }).click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  await thema.getByRole('button', { name: 'Systeem' }).click();
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(html).toHaveAttribute('data-theme', 'light');
});

test('"Terug naar de Hub": alleen met een geldig adres uit /_shc/config.json, in zijbalk en Meer-sheet (O-11)', async ({ adminPage: page }) => {
  // vite preview kent /_shc/config.json niet (nginx maakt het uit HUB_URL); hier speelt de test de webserver na.
  let config: unknown = { hub_url: 'https://hub.voorbeeld.invalid:8443' };
  await page.route('**/_shc/config.json', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(config) }));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await settled(page);
  const side = page.getByRole('navigation', { name: 'Hoofdmenu', exact: true });
  const link = side.getByRole('link', { name: 'Terug naar de Hub' });
  await expect(link).toHaveAttribute('href', 'https://hub.voorbeeld.invalid:8443');
  await expect(link).not.toHaveAttribute('target', /.+/);
  const box = await link.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await page.setViewportSize({ width: 390, height: 800 });
  await page.getByRole('navigation', { name: 'Hoofdmenu (mobiel)' }).getByRole('button', { name: 'Meer' }).click();
  await expect(page.getByRole('dialog', { name: 'Meer' }).getByRole('link', { name: 'Terug naar de Hub' })).toBeVisible();
  await axe(page, 'Meer-sheet met Hub-link');

  for (const bad of [{}, { hub_url: 'javascript:alert(1)' }, { hub_url: 'https://hub.voorbeeld.invalid/pad' }, { hub_url: 'http://127.0.0.1:8443' }]) {
    config = bad;
    const geladen = page.waitForResponse('**/_shc/config.json');
    await page.goto('/');
    await geladen;
    await settled(page);
    await expect(page.getByRole('link', { name: 'Terug naar de Hub' })).toHaveCount(0);
  }
});

test.describe('uitgelogd', () => {
  test('geen Hub-link en geen themaknop vóór het inloggen; het thema staat wel goed', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Inloggen' })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.getByRole('group', { name: 'Thema' })).toHaveCount(0);
  });
});
