// Flow 5: give a P1 meter the role "Huis totaal (P1)". The first binding needs no question; moving the
// role to a second meter asks first (the other meter is disconnected and marked "vervangen"), and so
// does unbinding (verbeterlijst 24 and kleine punten).
import { randomUUID } from 'node:crypto';
import { expect, seed, test, unique } from './helpers';

function seedP1(homeId: string, name: string) {
  return seed<{ id: string; name: string }>('device', {
    home_id: homeId,
    provider: 'homewizard',
    kind: 'p1_meter',
    name,
    model: 'HWE-P1',
    stable_identity: randomUUID().replace(/-/g, '').slice(0, 12),
    online: true,
    capabilities: ['power', 'energy'],
    state: {},
  });
}

test('meterrol koppelen, verplaatsen met bevestiging en loskoppelen', async ({ adminPage: page, api, homeId }) => {
  // Start without a grid meter (the test may run again on the same database).
  for (const d of await api.get<{ id: string; roles: string[] }[]>(`/homes/${homeId}/devices`)) {
    if (d.roles.includes('grid')) await api.send('POST', `/devices/${d.id}/unbind`, { role: 'grid' });
  }
  const u = unique();
  const first = seedP1(homeId, `P1-meter meterkast ${u}`);
  const second = seedP1(homeId, `P1-meter nieuw ${u}`);

  const dialogs: string[] = [];
  page.on('dialog', (d) => {
    dialogs.push(d.message());
    void (dialogs.length === 2 ? d.dismiss() : d.accept());
  });

  await page.goto('/apparaten');
  const meters = page.getByRole('region', { name: 'Energiemeters' });
  const firstCard = meters.getByRole('article', { name: first.name });
  const secondCard = meters.getByRole('article', { name: second.name });

  // The picker only offers roles that fit a P1 meter.
  const pick = firstCard.getByLabel(`Rol van ${first.name}`);
  await expect(pick.locator('option')).toHaveText(['Kies een rol…', 'Huis totaal (P1)']);
  await pick.selectOption({ label: 'Huis totaal (P1)' });
  await firstCard.getByRole('button', { name: `Rol koppelen: ${first.name}` }).click();
  await expect(firstCard.getByRole('button', { name: `Rol Huis totaal (P1) loskoppelen van ${first.name}` })).toBeVisible();
  expect(dialogs, 'een eerste koppeling vraagt niets').toHaveLength(0);

  // Moving the role asks first and says what happens to the other meter.
  const pick2 = secondCard.getByLabel(`Rol van ${second.name}`);
  await expect(pick2.locator('option').nth(1)).toHaveText(`Huis totaal (P1) (nu: ${first.name})`);
  await pick2.selectOption({ index: 1 });
  await secondCard.getByRole('button', { name: `Rol koppelen: ${second.name}` }).click();
  await expect.poll(() => dialogs.length).toBe(1);
  expect(dialogs[0]).toContain(`${second.name} koppelen als ‘Huis totaal (P1)’?`);
  expect(dialogs[0]).toContain(`${first.name} heeft deze rol nu. Die wordt losgekoppeld en als ‘vervangen’ gemarkeerd`);

  await expect(secondCard.getByRole('button', { name: `Rol Huis totaal (P1) loskoppelen van ${second.name}` })).toBeVisible();
  await expect(firstCard.getByRole('button', { name: /^Rol .* loskoppelen van / })).toHaveCount(0);
  await expect(firstCard.getByText('Vervangen', { exact: true })).toBeVisible();
  const devices = await api.get<{ id: string; roles: string[]; lifecycle: string }[]>(`/homes/${homeId}/devices`);
  expect(devices.find((d) => d.id === first.id)).toMatchObject({ roles: [], lifecycle: 'replaced' });
  expect(devices.find((d) => d.id === second.id)?.roles).toEqual(['grid']);

  // Unbinding asks too; "Annuleren" keeps the role.
  const unbind = secondCard.getByRole('button', { name: `Rol Huis totaal (P1) loskoppelen van ${second.name}` });
  await unbind.click();
  await expect.poll(() => dialogs.length).toBe(2);
  expect(dialogs[1]).toContain(`Rol ‘Huis totaal (P1)’ loskoppelen van ${second.name}?`);
  await expect(unbind).toBeVisible();
  await unbind.click();
  await expect.poll(() => dialogs.length).toBe(3);
  await expect(unbind).toHaveCount(0);
  await expect(secondCard.getByLabel(`Rol van ${second.name}`)).toBeVisible();
});
