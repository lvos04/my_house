// Flow 10: a 360 px wide phone. Thermostat buttons and other targets are at least 44 × 44 px, the
// pages do not scroll sideways, and logging out is reachable through "Meer" (verbeterlijst 37, 40).
import { ensureLinkedZone, expect, expectNoHorizontalScroll, test } from './helpers';
import type { Locator, Page } from '@playwright/test';

test.use({ viewport: { width: 360, height: 760 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });

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

test('telefoon 360 px: grote knoppen, geen zijwaartse scroll, uitloggen bereikbaar', async ({ adminPage: page, api, homeId }) => {
  const zone = await ensureLinkedZone(api, homeId, 'Slaapkamer');

  await page.goto('/');
  const card = page.getByRole('article', { name: 'Slaapkamer' });
  await expectTarget(card.getByRole('button', { name: 'Doeltemperatuur Slaapkamer lager' }), 'overzicht −');
  await expectTarget(card.getByRole('button', { name: 'Doeltemperatuur Slaapkamer hoger' }), 'overzicht +');
  await expectNoHorizontalScroll(page);

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
});
