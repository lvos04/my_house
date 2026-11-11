// Flow 12: tabel en toetsenbord bij de grafiek "Laatste 24 uur" (besluit van de gebruiker, inventaris C09; Peil 1.1.1
// §10; ADR-0013). Tegen de echte API: een airco met zes uur metingen (met een gat van 2½ uur en één meting zonder
// waarde) zoals de worker ze opslaat. Getest: het plotvak is één focusstop met een ring, ← → Home End Esc lezen per uur
// voor (aria-live) met een draadkruis, de aanwijzer kiest hetzelfde uur, "Toon als tabel, per uur" geeft dezelfde
// uren met "Geen meting" in het gat, en axe-core = 0 plus de Peil-matrix met open tabel en gekozen uur, licht en
// donker, 320–1440 px. Bij elke breedte: axe-core, de ring om het plotvak, en met de muis over de uren verspringt wat
// onder de grafiek staat niet. Op een
// telefoon (aanraken): een tik kiest een uur en dat blijft staan tot een tik ernaast.
// Met SHC_E2E_SHOTS=<map> worden afdrukken van de sectie bewaard (docs/screenshots/peil/review/12-grafiek-24-uur-*).
import type { Locator, Page } from '@playwright/test';
import { expect, seed, seedAirco, test, unique, type Api } from './helpers';
import { axe, contentOutside, focusStop, outsideCards, settled, smallTargets, smallText, widest } from './peil-metingen';

const TABEL = 'Laatste 24 uur per uur: gemeten temperatuur en doeltemperatuur';
// "11 nov 06:00–07:00"; één tijd als begin en eind in dezelfde minuut vallen (de test start precies op een heel uur).
const UUR = /^\d{1,2} \S+ \d\d:\d\d(–\d\d:\d\d)?$/;

/** Zes uur metingen elke 10 minuten, behalve tussen 270 en 120 minuten geleden (geen meting); 30 minuten geleden een
 * meting zonder waarde. Het doel gaat 60 minuten geleden van 21 naar 19. */
function reeksen() {
  const temperature: [number, number | null][] = [];
  const target: [number, number][] = [];
  for (let m = 360; m >= 0; m -= 10) {
    if (m < 270 && m > 120) continue;
    temperature.push([m, m === 30 ? null : Math.round((20 + Math.sin(m / 40)) * 10) / 10]);
    target.push([m, m > 60 ? 21 : 19]);
  }
  return { temperature, target };
}

async function openZone(page: Page, zoneId: string) {
  await page.goto(`/klimaat?zone=${zoneId}&tab=bediening`);
  await settled(page);
  const sectie = page.getByRole('region', { name: 'Laatste 24 uur' });
  const plot = sectie.getByRole('img', { name: /^Gemeten en doeltemperatuur, bereik \d+° tot \d+°, laatste meting \d+,\d°$/ });
  await expect(plot).toBeVisible();
  return { sectie, plot, lezing: sectie.getByTestId('hist-lezing') };
}

async function openTabel(sectie: Locator) {
  await sectie.getByText('Toon als tabel, per uur', { exact: true }).click();
  const tabel = sectie.getByRole('table', { name: TABEL });
  await expect(tabel).toBeVisible();
  return tabel;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Met de muis over alle uren, zonder focus: elk uur geeft een uitlezing en "Toon als tabel" eronder blijft op zijn
 * plaats (de ruimte voor de uitlezing is er al). Geeft de afwijkingen terug. */
async function muisOverUren(page: Page, sectie: Locator): Promise<string[]> {
  const fouten: string[] = [];
  await sectie.getByTestId('hist-plot').evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await page.mouse.move(2, 2);
  const lezing = sectie.getByTestId('hist-lezing');
  await expect(lezing).toHaveText('');
  const samenvatting = sectie.locator('summary');
  const voor = (await samenvatting.boundingBox())!.y;
  const vlak = (await sectie.locator('.hist-area').boundingBox())!;
  const gezien = new Set<string>();
  for (let i = 0; i <= 24; i++) {
    await page.mouse.move(vlak.x + 1 + ((vlak.width - 2) * i) / 24, vlak.y + vlak.height / 2);
    await expect(lezing).not.toHaveText('');
    gezien.add((await lezing.textContent())!);
    const na = (await samenvatting.boundingBox())!.y;
    if (Math.abs(na - voor) > 0.5) fouten.push(`"Toon als tabel" verspringt ${Math.round(na - voor)} px bij "${await lezing.textContent()}"`);
  }
  if (gezien.size < 5) fouten.push(`de muis bereikte maar ${gezien.size} uren`);
  await page.mouse.move(2, 2);
  await expect(lezing).toHaveText('');
  if (Math.abs((await samenvatting.boundingBox())!.y - voor) > 0.5) fouten.push('"Toon als tabel" verspringt als de muis de grafiek verlaat');
  return fouten;
}

let zoneId = '';

/** Eén nieuwe airco met de reeksen hierboven, gekoppeld aan de slaapkamer (zonder aparte sensor: de grafiek leest de
 * temperatuur van de unit). Eén keer per worker; de andere browsertests draaien eerder en merken er niets van. */
async function zoneMetMetingen(api: Api, homeId: string): Promise<string> {
  if (zoneId) return zoneId;
  const zone = await api.zone(homeId, 'Slaapkamer');
  const unit = seedAirco(homeId, `Airco slaapkamer ${unique()}`);
  await api.send('PATCH', `/zones/${zone.id}`, { unit_device_id: unit.id, sensor_device_id: null });
  const { temperature, target } = reeksen();
  const t = seed<{ added: number }>('readings', { device_id: unit.id, metric: 'temperature', unit: '°C', points: temperature });
  const d = seed<{ added: number }>('readings', { device_id: unit.id, metric: 'target_c', unit: '°C', points: target });
  expect(t.added).toBe(temperature.length);
  expect(d.added).toBe(target.length);
  zoneId = zone.id;
  return zoneId;
}

test.describe('grafiek "Laatste 24 uur"', () => {
  test('toetsenbord en aanwijzer lezen per uur; de tabel heeft dezelfde uren met "Geen meting" in het gat', async ({ adminPage: page, api, homeId }) => {
    const zone = await zoneMetMetingen(api, homeId);
    await page.setViewportSize({ width: 1440, height: 900 });
    const { sectie, plot, lezing } = await openZone(page, zone);
    await expect(plot).toHaveAccessibleDescription(/pijltjes naar links en rechts om per uur te lezen/);

    // Gat: gearceerd, met het woord erin (2½ uur van 6 past op 1440 px) en de uitleg eronder.
    const gat = sectie.getByTestId('hist-gat');
    await expect(gat).toHaveCount(1);
    await expect(gat.getByText('Geen meting')).toBeVisible();
    await expect(sectie.getByText('Gearceerd: geen meting (nooit 0).')).toBeVisible();

    // De tabel: één rij per uur, oudste eerst; het gat heeft in elke kolom "Geen meting".
    const tabel = await openTabel(sectie);
    await expect(tabel.getByRole('columnheader')).toHaveText(['Uur', 'Gemeten (gemiddeld)', 'Laagst – hoogst', 'Doel']);
    const uren = await tabel.getByRole('rowheader').allTextContents();
    expect(uren.length).toBeGreaterThanOrEqual(7);
    for (const u of uren) expect(u).toMatch(UUR);
    const rijen = tabel.getByRole('row');
    const gatRijen = rijen.filter({ has: page.getByRole('cell', { name: 'Geen meting', exact: true }) });
    expect(await gatRijen.count()).toBeGreaterThanOrEqual(1);
    await expect(gatRijen.first().getByRole('cell')).toHaveText(['Geen meting', 'Geen meting', 'Geen meting']);
    await expect(rijen.nth(1).getByRole('cell').last()).toHaveText('21,0°');
    await expect(rijen.last().getByRole('cell').last()).toHaveText('19,0°');
    for (const c of await rijen.nth(1).getByRole('cell').allTextContents()) expect(c).toMatch(/^\d+,\d°( – \d+,\d°)?$/);
    // Op een breed scherm loopt de scheidingslijn van een rij ook onder de kolom "Uur" door (rijkop = cellen).
    const lijnen = await rijen.nth(2).locator('th, td').evaluateAll((els) => els.map((e) => `${getComputedStyle(e).borderTopWidth} ${getComputedStyle(e).borderTopStyle}`));
    expect(lijnen).toEqual(['1px solid', '1px solid', '1px solid', '1px solid']);

    // Aanwijzer (zonder focus): links in het plotvak = het eerste uur, weg = leeg.
    const vlak = (await sectie.locator('.hist-area').boundingBox())!;
    await page.mouse.move(vlak.x + 2, vlak.y + vlak.height / 2);
    await expect(lezing).toHaveText(new RegExp(`^${escape(uren[0])}: gemeten`));
    await expect(sectie.getByTestId('hist-kruis')).toBeVisible();
    await page.mouse.move(vlak.x + vlak.width / 2, vlak.y + vlak.height / 2);
    await expect(lezing).toHaveText(new RegExp(`^(${uren.map(escape).join('|')}): `));
    await page.mouse.move(5, 5);
    await expect(lezing).toHaveText('');

    // Toetsenbord: het plotvak is een gewone Tab-stop (Shift+Tab eruit en Tab terug), met een ring ≥ 3:1 die niet
    // bedekt is.
    await plot.focus();
    await page.keyboard.press('Shift+Tab');
    await expect(plot).not.toBeFocused();
    await page.keyboard.press('Tab');
    await expect(plot).toBeFocused();
    expect(await plot.evaluate((el) => el.matches(':focus-visible'))).toBe(true);
    const stop = (await focusStop(page))!;
    expect(stop.ring).toBeGreaterThanOrEqual(2);
    expect(stop.contrast).toBeGreaterThanOrEqual(3);
    expect(stop.bedekt).toBe(false);
    // Uitleg zichtbaar zolang er nog geen uur gekozen is (daarna alleen nog als beschrijving voor de schermlezer).
    const uitleg = sectie.getByText(/^Gebruik de pijltjes naar links en rechts/);
    await expect(uitleg).toHaveClass('hist-hint');
    expect((await uitleg.boundingBox())!.width).toBeGreaterThan(100);

    await page.keyboard.press('Home');
    for (const u of uren) {
      await expect(lezing).toHaveText(new RegExp(`^${escape(u)}: `));
      await page.keyboard.press('ArrowRight');
    }
    await expect(lezing).toHaveText(new RegExp(`^${escape(uren.at(-1)!)}: `)); // blijft op het laatste uur
    await expect(uitleg).toHaveClass('sr-only');
    // Het gat leest "Geen meting" voor, net als de tabel.
    const gatUur = (await gatRijen.first().getByRole('rowheader').textContent())!;
    await page.keyboard.press('Home');
    for (let i = 0; i < uren.indexOf(gatUur); i++) await page.keyboard.press('ArrowRight');
    await expect(lezing).toHaveText(`${gatUur}: Geen meting`);
    // Het draadkruis staat binnen het gearceerde gat.
    const kruis = (await sectie.getByTestId('hist-kruis').boundingBox())!;
    const gatVak = (await gat.boundingBox())!;
    expect(kruis.x).toBeGreaterThanOrEqual(gatVak.x - 1);
    expect(kruis.x + kruis.width).toBeLessThanOrEqual(gatVak.x + gatVak.width + 1);
    await page.keyboard.press('End');
    await expect(lezing).toHaveText(new RegExp(`^${escape(uren.at(-1)!)}: gemeten .*; doel 19,0°$`));
    await page.keyboard.press('ArrowLeft');
    await expect(lezing).toHaveText(new RegExp(`^${escape(uren.at(-2)!)}: `));
    await page.keyboard.press('Escape');
    await expect(lezing).toHaveText('');
    await expect(sectie.getByTestId('hist-kruis')).toHaveCount(0);
    // Tab gaat verder naar "Toon als tabel" (geen focusval) en de keuze vervalt.
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('Tab');
    await expect(sectie.locator('summary')).toBeFocused();
    await expect(lezing).toHaveText('');
  });

  for (const [scheme, thema] of [['light', 'licht'], ['dark', 'donker']] as const) {
    test(`axe 0 en de Peil-matrix met open tabel en gekozen uur · ${thema}`, async ({ adminPage: page, api, homeId }) => {
      test.setTimeout(180_000);
      const zone = await zoneMetMetingen(api, homeId);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      const fouten: string[] = [];
      for (const width of [320, 360, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: width >= 1024 ? 900 : 800 });
        const { sectie, plot, lezing } = await openZone(page, zone);
        await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
        await openTabel(sectie);
        for (const f of await muisOverUren(page, sectie)) fouten.push(`${width} px: ${f}`);
        await plot.focus();
        await page.keyboard.press('End');
        await expect(lezing).not.toHaveText('');
        // Bij elke breedte en in elk thema: de ring om het plotvak (≥ 2 px, ≥ 3:1, niet bedekt) en axe-core = 0.
        const stop = await focusStop(page);
        if (!stop || stop.ring < 2 || stop.contrast < 3 || stop.bedekt) fouten.push(`${width} px: focusring van het plotvak ${JSON.stringify(stop)}`);
        await axe(page, `Klimaat › Laatste 24 uur (${thema}, ${width} px)`);
        const overloop = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        if (overloop > 1) fouten.push(`${width} px: ${overloop} px zijwaartse scroll (${await widest(page)})`);
        for (const f of await outsideCards(page)) fouten.push(`${width} px: buiten de kaart ${f}`);
        for (const f of await smallText(page)) fouten.push(`${width} px: tekst ${f}`);
        for (const f of await smallTargets(page)) fouten.push(`${width} px: doel ${f}`);
        for (const f of await contentOutside(page)) fouten.push(`${width} px: inhoud ${f}`);
        // Het woord in het gat staat er helemaal of niet (nooit half): als het er staat, past het in het gat.
        const woord = await sectie.getByTestId('hist-gat').evaluate((g) => {
          const s = g.querySelector('span')!;
          if (getComputedStyle(s).display === 'none') return null;
          return { gat: g.getBoundingClientRect().width, woord: s.getBoundingClientRect().width };
        });
        if (woord && woord.woord > woord.gat) fouten.push(`${width} px: "Geen meting" (${Math.round(woord.woord)} px) past niet in het gat (${Math.round(woord.gat)} px)`);
        const dir = process.env.SHC_E2E_SHOTS;
        if (dir && [390, 1440].includes(width)) {
          // Venster even zo hoog als de pagina: de vaste ondernavigatie staat dan onderaan en bedekt de sectie niet.
          await page.setViewportSize({ width, height: await page.evaluate(() => document.documentElement.scrollHeight) });
          await sectie.screenshot({ path: `${dir}/12-grafiek-24-uur-${thema}-${width}.png`, animations: 'disabled' });
        }
      }
      expect(fouten).toEqual([]);
    });
  }
});

test.describe('grafiek "Laatste 24 uur" op een telefoon (aanraken)', () => {
  test.use({ viewport: { width: 390, height: 800 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });

  test('een tik kiest een uur en de uitlezing blijft staan tot een tik ernaast', async ({ adminPage: page, api, homeId }) => {
    const zone = await zoneMetMetingen(api, homeId);
    const { sectie, plot, lezing } = await openZone(page, zone);
    await plot.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    const vlak = (await sectie.locator('.hist-area').boundingBox())!;
    const midden = vlak.y + vlak.height / 2;
    // Links = het eerste uur. De vinger gaat meteen weer weg (pointerleave): de keuze blijft staan.
    await page.touchscreen.tap(vlak.x + 3, midden);
    await expect(lezing).toHaveText(/^\d{1,2} \S+ \d\d:\d\d(–\d\d:\d\d)?: gemeten /);
    await expect(sectie.getByTestId('hist-kruis')).toBeVisible();
    const eerste = await lezing.textContent();
    // Rechts = het laatste uur (doel 19).
    await page.touchscreen.tap(vlak.x + vlak.width - 3, midden);
    await expect(lezing).toHaveText(/; doel 19,0°$/);
    expect(await lezing.textContent()).not.toBe(eerste);
    await expect(sectie.getByTestId('hist-kruis')).toHaveCount(1);
    // Een tik naast de grafiek: de keuze vervalt.
    await sectie.getByRole('heading', { name: 'Laatste 24 uur' }).tap();
    await expect(lezing).toHaveText('');
    await expect(sectie.getByTestId('hist-kruis')).toHaveCount(0);
  });
});
