// Compose only (infra/scripts/e2e_compose.sh): a command from the browser is executed by the worker and
// confirmed by the airco. The fake Gree unit of infra/e2e/compose.e2e.yaml is paired on the devices
// page (API → UDP on the "home network"), linked to a zone, and a higher target temperature set in the
// browser must reach "Bevestigd": queued by the API, sent by the worker, read back from the unit and
// reported over the WebSocket through nginx (verbeterlijst 23, 42). Without a worker it never gets there.
// (A setpoint, not a mode: the other flows leave a heating unit in another zone on the same outdoor
// unit, which rightly blocks cooling here.)
import { expect, test } from '../../apps/web/e2e/helpers';

const UNIT_HOST = process.env.E2E_FAKE_GREE_HOST ?? '192.168.77.10';
const UNIT_MAC = 'f4911e000001'; // integrations/tests/fake_gree.py

test('commando via de worker, bevestigd door de airco', async ({ adminPage: page, api, homeId }) => {
  test.setTimeout(180_000);

  // 1. Pair the unit on the devices page, as at home.
  await page.goto('/apparaten');
  const card = page.getByRole('article', { name: 'Gree airco' });
  await card.getByLabel('IP-adres binnenunit').fill(UNIT_HOST);
  await card.getByLabel('Naam (optioneel)').fill('Airco compose');
  await card.locator('form').getByRole('button', { name: 'Koppelen' }).click();
  await expect(card.getByRole('status')).toContainText('Gekoppeld:', { timeout: 30_000 });

  const devices = await api.get<{ id: string; stable_identity: string; integration_id: string | null }[]>(`/homes/${homeId}/devices`);
  const unit = devices.find((d) => d.stable_identity === UNIT_MAC);
  expect(unit?.integration_id, 'de airco is gekoppeld met een eigen integratie').toBeTruthy();

  const zone = await api.zone(homeId, 'Woonkamer');
  await api.send('PATCH', `/zones/${zone.id}`, { unit_device_id: unit!.id });
  try {
    // 2. The worker reads the unit (it starts heating at 21 °C), so the setpoint can be changed.
    await page.goto(`/klimaat?zone=${zone.id}&tab=bediening`);
    const control = page.getByRole('region', { name: 'Bediening' });
    const higher = control.getByRole('button', { name: 'Doeltemperatuur hoger' });
    await expect(higher).toBeEnabled({ timeout: 60_000 });
    const before = await api.zone(homeId, 'Woonkamer');
    expect(before.target_c, 'doeltemperatuur zoals de unit die meldt').toEqual(expect.any(Number));

    // 3. One step up: the status line says "Bevestigd" only on the worker's confirmed readback.
    await higher.click();
    await expect(control.locator('.c-status.confirmed')).toContainText('Bevestigd', { timeout: 60_000 });

    // The unit really has the new target now (the zone shows the unit's own read-back state).
    const wanted = before.target_c + before.capabilities.step_c;
    await expect.poll(async () => (await api.zone(homeId, 'Woonkamer')).target_c, { timeout: 30_000 }).toBe(wanted);
  } finally {
    // Leave the zone as the other flows expect it (no unit linked).
    await api.send('PATCH', `/zones/${zone.id}`, { unit_device_id: null });
  }
});
