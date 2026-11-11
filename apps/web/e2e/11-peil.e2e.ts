// Flow 11: ontwerptaal Peil (docs/ontwerp/PEIL_MIGRATIE.md §12, PEIL_REVIEW.md). Toegankelijkheid met axe-core op de
// hoofdschermen (WCAG 2.0/2.1/2.2 A en AA) in licht en donker op 390 en 1440 px, de Peil-testmatrix
// (geen zijwaartse scroll, geen tekst < 14 px, bedienbare doelen ≥ 44 × 44 px, inhoud binnen zijn knop of tegel),
// een volledige Tab-doorloop (ring ≥ 3:1, niet achter een vaste balk) en de twee toegestane
// toevoegingen: de themaknop Systeem / Licht / Donker (O-09) en de link "Terug naar de Hub" (O-11).
import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
import { ensureLinkedZone, expect, seed, test, unique } from './helpers';
import { axe, contentOutside, focusStop, outsideCards, settled, smallTargets, smallText, widest } from './peil-metingen';

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

/** Een kleur (ook een CSS-variabele) zoals de browser hem uitrekent, als "rgb(…)". */
async function kleur(page: Page, css: string): Promise<string> {
  return page.evaluate((c) => {
    const el = document.createElement('span');
    el.style.color = c;
    document.body.append(el);
    const uit = getComputedStyle(el).color;
    el.remove();
    return uit;
  }, css);
}

/** Contrast tussen twee kleuren (WCAG 2.x), beide als CSS-waarde. */
async function contrast(page: Page, a: string, b: string): Promise<number> {
  return page.evaluate(([x, y]) => {
    const c = document.createElement('canvas').getContext('2d')!;
    const rgb = (s: string) => { c.clearRect(0, 0, 1, 1); c.fillStyle = '#000'; c.fillStyle = s; c.fillRect(0, 0, 1, 1); return Array.from(c.getImageData(0, 0, 1, 1).data); };
    const lum = (v: number[]) => { const f = (q: number) => { q /= 255; return q <= 0.04045 ? q / 12.92 : Math.pow((q + 0.055) / 1.055, 2.4); }; return 0.2126 * f(v[0]) + 0.7152 * f(v[1]) + 0.0722 * f(v[2]); };
    const opgelost = (s: string) => { const el = document.createElement('span'); el.style.color = s; document.body.append(el); const u = getComputedStyle(el).color; el.remove(); return u; };
    const la = lum(rgb(opgelost(x))), lb = lum(rgb(opgelost(y)));
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }, [a, b] as const);
}

test('Peil 1.1.1: -soft-band ≥ 7:1 met -ink, rand -ink bij een gekozen modus en een brandende lichtgroep, ondernavigatie zonder extra gewicht', async ({ adminPage: page, api, homeId }) => {
  const zone = await ensureLinkedZone(api, homeId, 'Slaapkamer');
  const groep = `Bureau ${unique()}`;
  seed('device', {
    home_id: homeId, provider: 'hue', kind: 'light_group', name: groep, stable_identity: randomUUID(), online: true,
    state: { type: 'grouped_light', on: true, brightness: 60, owner_type: 'room', lights_on: 1, lights_total: 1, lights_reachable: 1 },
  });
  for (const [scheme, thema] of [['light', 'licht'], ['dark', 'donker']] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(`/klimaat?zone=${zone.id}`);
    await settled(page);
    // Tekst op een schemaband (my_house zet labels in de band) staat in -ink: ≥ 7:1 (E14; was 4,9–6,2:1).
    for (const d of ['heat', 'cool']) {
      expect(await contrast(page, `var(--domain-${d}-ink)`, `var(--domain-${d}-soft)`), `${thema} ${d}-ink op ${d}-soft`).toBeGreaterThanOrEqual(7);
    }
    // De gekozen modusknop (verwarmen of koelen) heeft een rand in -ink, niet in de domeinkleur (§4.1 regel 11).
    const gekozen = page.getByRole('region', { name: 'Bediening' }).locator('.mode-btn[aria-pressed="true"]');
    await expect(gekozen).toHaveCount(1);
    const soort = (await gekozen.getAttribute('class'))!.includes('cool') ? 'cool' : 'heat';
    expect(await gekozen.evaluate((el) => el.className), `${thema}: gekozen modus is verwarmen of koelen`).toMatch(/\b(heat|cool)\b/);
    expect(await gekozen.evaluate((el) => getComputedStyle(el).borderTopColor), `${thema} rand gekozen modus`).toBe(await kleur(page, `var(--domain-${soort}-ink)`));

    // Lichttegels staan op het Overzicht (LichtTegel C-45); de lichtgroep hierboven brandt.
    await page.goto('/');
    await settled(page);
    const tegel = page.locator('main .light-tile.on').first();
    await expect(tegel).toBeVisible();
    expect(await tegel.evaluate((el) => getComputedStyle(el).borderTopColor), `${thema} rand brandende lichtgroep`).toBe(await kleur(page, 'var(--domain-light-on-ink)'));
  }

  // Ondernavigatie op 320 px: items op labelbreedte (geen vijf gelijke kolommen), het actieve item even zwaar als de
  // andere, precies één aria-current.
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto('/');
  await settled(page);
  const items = page.locator('nav.bottom-nav .bottom-link');
  await expect(items).toHaveCount(5);
  const maten = await items.evaluateAll((els) => els.map((el) => ({
    breed: Math.round(el.getBoundingClientRect().width),
    gewicht: getComputedStyle(el).fontWeight,
    actief: el.getAttribute('aria-current') === 'page',
  })));
  expect(maten.filter((m) => m.actief)).toHaveLength(1);
  expect(new Set(maten.map((m) => m.gewicht)).size, 'actief zonder extra gewicht').toBe(1);
  expect(new Set(maten.map((m) => m.breed)).size, 'items op labelbreedte, geen vaste kolommen').toBeGreaterThan(1);
  expect(Math.min(...maten.map((m) => m.breed)), 'elk item minstens 44 px').toBeGreaterThanOrEqual(44);
  expect(await contentOutside(page)).toEqual([]);
});

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
