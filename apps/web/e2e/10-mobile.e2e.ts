// Flow 10: phones of 360 and 320 px wide. Thermostat buttons and other targets are at least 44 × 44 px,
// the thermostat and the cards stay inside their card / the page, the pages do not scroll sideways,
// and logging out is reachable through "Meer" (verbeterlijst 37, 40).
import { ensureLinkedZone, expect, expectNoHorizontalScroll, test, type Api } from './helpers';
import type { Locator, Page } from '@playwright/test';

async function expectTarget(l: Locator, what: string) {
  await expect(l, what).toBeVisible();
  const box = await l.boundingBox();
  expect(box, what).not.toBeNull();
  expect(box!.width, `${what}: breedte`).toBeGreaterThanOrEqual(44);
  expect(box!.height, `${what}: hoogte`).toBeGreaterThanOrEqual(44);
}

async function expectInViewport(page: Page, l: Locator, what: string) {
  const box = await l.boundingBox();
  const vp = page.viewportSize()!;
  expect(box, what).not.toBeNull();
  expect(box!.x, `${what}: links`).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width, `${what}: rechts`).toBeLessThanOrEqual(vp.width + 0.5);
  expect(box!.y + box!.height, `${what}: onder`).toBeLessThanOrEqual(vp.height + 0.5);
}

/** `l` lies within the content box (inside the padding) of `box`. */
async function expectInside(l: Locator, box: Locator, what: string) {
  const inner = await box.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const px = (v: string) => parseFloat(v) || 0;
    return {
      left: r.left + px(cs.borderLeftWidth) + px(cs.paddingLeft),
      right: r.right - px(cs.borderRightWidth) - px(cs.paddingRight),
    };
  });
  const b = await l.boundingBox();
  expect(b, what).not.toBeNull();
  expect(b!.x, `${what}: links binnen de kaart`).toBeGreaterThanOrEqual(inner.left - 0.5);
  expect(b!.x + b!.width, `${what}: rechts binnen de kaart`).toBeLessThanOrEqual(inner.right + 0.5);
}

/** No card sticks out of the page's content area (e.g. a 300 px grid column on a 320 px phone). */
async function expectCardsFit(page: Page, what: string) {
  const outside = await page.evaluate(() => {
    const main = document.querySelector('main')!;
    const r = main.getBoundingClientRect();
    const cs = getComputedStyle(main);
    const left = r.left + parseFloat(cs.paddingLeft);
    const right = r.right - parseFloat(cs.paddingRight);
    return Array.from(main.querySelectorAll<HTMLElement>('.card'))
      .map((c) => ({ c, b: c.getBoundingClientRect() }))
      .filter(({ b }) => b.width > 0 && (b.left < left - 0.5 || b.right > right + 0.5))
      .map(({ c, b }) => `${c.getAttribute('aria-label') ?? c.querySelector('h2,h3')?.textContent ?? c.className}: ${b.left.toFixed(0)}–${b.right.toFixed(0)} (ruimte ${left.toFixed(0)}–${right.toFixed(0)})`);
  });
  expect(outside, `${what}: kaarten buiten de pagina`).toEqual([]);
}

for (const width of [360, 320]) {
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height: 760 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });

    test(`telefoon ${width} px: grote knoppen, alles binnen beeld, uitloggen bereikbaar`, async ({ adminPage: page, api, homeId }) => {
      await phoneFlow(page, api, homeId);
    });
  });
}

async function phoneFlow(page: Page, api: Api, homeId: string) {
  const zone = await ensureLinkedZone(api, homeId, 'Slaapkamer');

  await page.goto('/');
  const card = page.getByRole('article', { name: 'Slaapkamer' });
  await expectTarget(card.getByRole('button', { name: 'Doeltemperatuur Slaapkamer lager' }), 'overzicht −');
  await expectTarget(card.getByRole('button', { name: 'Doeltemperatuur Slaapkamer hoger' }), 'overzicht +');
  await expectNoHorizontalScroll(page);
  await expectCardsFit(page, 'overzicht');

  // Phones get the bottom bar instead of the sidebar.
  const bottom = page.getByRole('navigation', { name: 'Hoofdmenu (mobiel)' });
  await expect(page.getByRole('navigation', { name: 'Hoofdmenu', exact: true })).toBeHidden();
  for (const name of ['Overzicht', 'Klimaat', 'Licht', 'Energie']) {
    await expectTarget(bottom.getByRole('link', { name }), `onderbalk ${name}`);
    await expectInViewport(page, bottom.getByRole('link', { name }), `onderbalk ${name}`);
  }
  await expectTarget(bottom.getByRole('button', { name: 'Meer' }), 'onderbalk Meer');

  // Klimaat: the large thermostat, mode buttons and actions.
  await bottom.getByRole('link', { name: 'Klimaat' }).click();
  await page.getByRole('tablist', { name: 'Zone' }).getByRole('tab', { name: zone.name }).click();
  await expect(page.getByRole('heading', { level: 1, name: zone.name })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`zone=${zone.id}`));
  const control = page.getByRole('region', { name: 'Bediening' });
  await expectTarget(control.getByRole('button', { name: 'Doeltemperatuur lager' }), 'klimaat −');
  await expectTarget(control.getByRole('button', { name: 'Doeltemperatuur hoger' }), 'klimaat +');
  // The −/+ buttons and the dial stay inside the card; the dial shrinks, the buttons do not.
  await expectInside(control.getByRole('button', { name: 'Doeltemperatuur lager' }), control, 'klimaat −');
  await expectInside(control.getByRole('button', { name: 'Doeltemperatuur hoger' }), control, 'klimaat +');
  await expectInside(control.locator('.c-dial'), control, 'draaiknop');
  await expectCardsFit(page, 'klimaat');
  for (const b of await control.getByRole('group', { name: 'Modus' }).getByRole('button').all()) {
    await expectTarget(b, `modus ${await b.textContent()}`);
  }
  for (const b of await control.getByRole('group', { name: 'Ventilator' }).getByRole('button').all()) {
    await expectTarget(b, `ventilator ${await b.textContent()}`);
  }
  await expectTarget(control.getByRole('button', { name: 'Handmatig 2 uur' }), 'Handmatig');
  await expectNoHorizontalScroll(page);

  // Other pages fit too.
  for (const path of ['/energie', '/verlichting', '/apparaten', '/systeem', '/account']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectCardsFit(page, path);
  }

  // Log out through "Meer".
  await page.goto('/');
  await bottom.getByRole('button', { name: 'Meer' }).click();
  const sheet = page.getByRole('dialog', { name: 'Meer' });
  await expect(sheet).toBeVisible();
  const logout = sheet.getByRole('button', { name: 'Uitloggen' });
  await expectTarget(logout, 'Uitloggen');
  await expectInViewport(page, logout, 'Uitloggen');
  await logout.click();
  await expect(page.getByRole('heading', { name: 'Inloggen' })).toBeVisible();
  await expectNoHorizontalScroll(page);
}
