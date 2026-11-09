// Flow 8: the API goes away (process stopped). After the offline threshold (3 failed attempts or 15 s,
// verbeterlijst 4) the banner says so, every control is disabled and the last known data stays on
// screen. When the API is back, the app recovers by itself (WebSocket reconnect, data refetched).
import { apiProcess, ensureLinkedZone, expect, seed, test, unique } from './helpers';

test('offline: API gestopt, banner, bediening uit, laatste gegevens zichtbaar, en herstel', async ({ adminPage: page, api, homeId }) => {
  test.setTimeout(180_000);
  const zone = await ensureLinkedZone(api, homeId, 'Slaapkamer');
  // A fresh worker heartbeat (test data, no worker runs here): "Achtergronddienst" is measured healthy on the
  // overview, so the "never green while offline" check below really tests something (PEIL_REVIEW finding 1).
  // Removed again at the end, so the following tests see the same state as before.
  seed('heartbeat', {});

  const ws = page.waitForEvent('websocket', (w) => w.url().includes('/api/v1/ws'));
  await page.goto('/');
  await (await ws).waitForEvent('framereceived', { predicate: (f) => String(f.payload).includes('"hello"') });
  const card = page.getByRole('article', { name: 'Slaapkamer' });
  await expect(card.getByRole('button', { name: 'Doeltemperatuur Slaapkamer hoger' })).toBeEnabled();
  // Online staat er minstens één gemeten "Gezond" op het overzicht; anders zegt de controle offline hieronder niets.
  const healthy = await page.locator('main [data-status="healthy"]').count();
  expect(healthy, 'online minstens één Gezond op het overzicht').toBeGreaterThan(0);
  await page.getByRole('navigation', { name: 'Hoofdmenu', exact: true }).getByRole('link', { name: 'Klimaat' }).click();
  await page.getByRole('tablist', { name: 'Zone' }).getByRole('tab', { name: 'Slaapkamer' }).click();
  const control = page.getByRole('region', { name: 'Bediening' });
  const heat = control.getByRole('group', { name: 'Modus' }).getByRole('button', { name: 'Verwarmen' });
  await expect(heat).toBeEnabled();
  await expect(control.getByRole('button', { name: 'Handmatig 2 uur' })).toBeEnabled();
  await expect(page.getByRole('status').filter({ hasText: 'Geen verbinding met de server' })).toHaveCount(0);

  const stoppedAt = Date.now();
  apiProcess('stop');
  try {
    const banner = page.getByRole('status').filter({ hasText: 'Geen verbinding met de server' });
    await expect(banner).toBeVisible({ timeout: 40_000 });
    // Not at the first failed attempt: the reconnects back off (≥ 0.5 s, ≥ 1 s) before the third one.
    expect(Date.now() - stoppedAt).toBeGreaterThanOrEqual(1_000);
    await expect(banner).toContainText(/laatst bekend \d{2}:\d{2}\)\. Bediening is uitgeschakeld\./);

    // Last known data stays; every action is disabled.
    await expect(page.getByRole('heading', { level: 1, name: 'Slaapkamer' })).toBeVisible();
    await expect(control.locator('.c-dial .big-num')).toHaveText(/21/);
    await expect(heat).toBeDisabled();
    await expect(control.getByRole('button', { name: 'Doeltemperatuur hoger' })).toBeDisabled();
    await expect(control.getByRole('button', { name: 'Handmatig 2 uur' })).toBeDisabled();
    await expect(page.locator('.shell')).toHaveClass(/is-offline/);

    // Another page from the cache: the overview as last seen, controls off.
    await page.getByRole('navigation', { name: 'Hoofdmenu', exact: true }).getByRole('link', { name: 'Overzicht' }).click();
    await expect(page.getByRole('article', { name: 'Slaapkamer' })).toBeVisible();
    await expect(page.getByRole('article', { name: 'Slaapkamer' }).getByRole('button', { name: 'Doeltemperatuur Slaapkamer hoger' })).toBeDisabled();
    await expect(banner).toBeVisible();
    // Peil (docs/ontwerp/PEIL_REVIEW.md): de laatst bekende stand is nooit groen, en de kaarten krijgen de
    // gestippelde Verouderd-rand.
    await expect(page.locator('main [data-status="healthy"]')).toHaveCount(0);
    await expect.poll(() => page.locator('main [data-status="stale"]').count()).toBeGreaterThanOrEqual(healthy);
    await expect(page.getByRole('article', { name: 'Slaapkamer' })).toHaveCSS('border-top-style', 'dashed');

    // A page that was never fetched: Onbekend with the reason and a heading, not an endless "Laden…".
    await page.getByRole('navigation', { name: 'Hoofdmenu', exact: true }).getByRole('link', { name: 'Verlichting' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Verlichting' })).toBeVisible();
    await expect(page.getByTestId('nooit-opgehaald').first()).toContainText('Onbekend — nog niet opgehaald');
    await expect(page.getByText('Laden…')).toHaveCount(0);
    await expect(banner).toBeVisible();
    await page.getByRole('navigation', { name: 'Hoofdmenu', exact: true }).getByRole('link', { name: 'Overzicht' }).click();
    await expect(page.getByRole('article', { name: 'Slaapkamer' })).toBeVisible();
  } finally {
    apiProcess('start');
    seed('heartbeat', { clear: true });
  }

  // Back: the banner goes away by itself and the controls work again (reconnect backs off up to 30 s).
  await expect(page.getByRole('status').filter({ hasText: 'Geen verbinding met de server' })).toHaveCount(0, { timeout: 60_000 });
  await expect(page.locator('.shell')).not.toHaveClass(/is-offline/);
  await expect(page.getByRole('article', { name: 'Slaapkamer' }).getByRole('button', { name: 'Doeltemperatuur Slaapkamer hoger' })).toBeEnabled();
  const fresh = await api.zone(homeId, 'Slaapkamer');
  expect(fresh.id).toBe(zone.id);

  // Live updates work again over the new connection.
  const key = `e2e-offline:${unique()}`;
  seed('alert', { home_id: homeId, key, severity: 'info', title: `Na herstel ${key.slice(12)}` });
  const alerts = page.getByRole('region', { name: 'Meldingen' });
  await expect(alerts.getByText(`Na herstel ${key.slice(12)}`)).toBeVisible({ timeout: 10_000 });
  seed('resolve', { key });
  await expect(alerts.getByText(`Na herstel ${key.slice(12)}`)).toHaveCount(0, { timeout: 10_000 });
});
