// Flow 9: an admin adds a viewer account ("Kijker"); the viewer sees the house but no controls and no
// management anywhere, and the server refuses a command from a viewer anyway (verbeterlijst 18, 43).
import { randomUUID } from 'node:crypto';
import { ensureLinkedZone, expect, seed, test, unique, VIEWER } from './helpers';

test('kijk-account ziet geen bediening', async ({ adminPage: page, api, homeId }) => {
  // Something to (not) control: an airco zone, a light group and an open notification.
  const zone = await ensureLinkedZone(api, homeId, 'Slaapkamer');
  const u = unique();
  seed('device', {
    home_id: homeId,
    provider: 'hue',
    kind: 'light_group',
    name: `Keuken ${u}`,
    stable_identity: randomUUID(),
    online: true,
    state: { type: 'grouped_light', on: true, brightness: 60, owner_type: 'room', lights_on: 1, lights_total: 1, lights_reachable: 1 },
  });
  seed('alert', { home_id: homeId, key: `e2e-viewer:${u}`, severity: 'info', title: `Melding voor kijker ${u}`, detail: 'Alleen lezen.' });
  for (const usr of await api.get<{ id: string; username: string }[]>('/users')) {
    if (usr.username === VIEWER.username) await api.send('DELETE', `/users/${usr.id}`);
  }

  // The admin adds the viewer on the account page.
  await page.goto('/account#gebruikers');
  const add = page.getByRole('form', { name: 'Gebruiker toevoegen' });
  await add.getByLabel('Gebruikersnaam').fill(VIEWER.username);
  await add.getByLabel('Wachtwoord (min. 12 tekens)').fill(VIEWER.password);
  await expect(add.getByLabel('Rol')).toHaveValue('viewer');
  await add.getByRole('button', { name: 'Toevoegen' }).click();
  await expect(add.getByRole('status')).toHaveText('Gebruiker toegevoegd.');
  await expect(page.getByRole('row').filter({ hasText: VIEWER.username })).toBeVisible();

  // Log out and in again as the viewer.
  await page.getByRole('navigation', { name: 'Hoofdmenu', exact: true }).getByRole('button', { name: 'Uitloggen' }).click();
  await expect(page.getByRole('heading', { name: 'Inloggen' })).toBeVisible();
  await page.getByLabel('Gebruikersnaam').fill(VIEWER.username);
  await page.getByLabel('Wachtwoord').fill(VIEWER.password);
  await page.getByRole('button', { name: 'Inloggen' }).click();
  const nav = page.getByRole('navigation', { name: 'Hoofdmenu', exact: true });
  await expect(nav.getByText(`Ingelogd als ${VIEWER.username} (Kijker)`)).toBeVisible();

  // Overzicht: values, but no +/−, no light switching, no "Gezien", no "Apparaat toevoegen".
  await nav.getByRole('link', { name: 'Overzicht' }).click();
  const main = page.getByRole('main');
  const card = main.getByRole('article', { name: 'Slaapkamer' });
  await expect(card).toBeVisible();
  await expect(card.getByText('21', { exact: false }).first()).toBeVisible();
  await expect(card.getByRole('button')).toHaveCount(0);
  await expect(main.getByRole('region', { name: 'Verlichting' }).getByText(`Keuken ${u}`)).toBeVisible();
  await expect(main.getByRole('region', { name: 'Verlichting' }).getByRole('button')).toHaveCount(0);
  const alerts = main.getByRole('region', { name: 'Meldingen' });
  await expect(alerts.getByText(`Melding voor kijker ${u}`)).toBeVisible();
  await expect(alerts.getByRole('button')).toHaveCount(0);
  await expect(main.getByRole('link', { name: 'Apparaat toevoegen' })).toHaveCount(0);

  // Klimaat: mode and fan as facts, no settings tab, schedule read-only.
  await page.goto(`/klimaat?zone=${zone.id}`);
  const control = main.getByRole('region', { name: 'Bediening' });
  await expect(control.getByText('Verwarmen')).toBeVisible();
  await expect(control.getByRole('button')).toHaveCount(0);
  const parts = main.getByRole('tablist', { name: 'Onderdelen van Slaapkamer' });
  await expect(parts.getByRole('tab')).toHaveText(['Bediening', 'Weekschema', 'Comfortprofielen']);
  await parts.getByRole('tab', { name: 'Weekschema' }).click();
  await expect(main.getByText('Alleen een beheerder kan het schema wijzigen.')).toBeVisible();
  await expect(main.getByRole('button', { name: 'Weekschema opslaan' })).toHaveCount(0);
  await expect(main.getByRole('button', { name: 'Sjabloon toepassen' })).toHaveCount(0);

  // Verlichting, Energie, Apparaten, Systeem: nothing to switch or manage.
  await page.goto('/verlichting');
  const group = main.getByRole('article', { name: `Keuken ${u}` });
  await expect(group.getByText('Aan', { exact: true })).toBeVisible();
  await expect(group.getByRole('button')).toHaveCount(0);
  await expect(group.getByRole('slider')).toHaveCount(0);
  await expect(main.getByRole('button', { name: /^Simuleer / })).toHaveCount(0);

  await page.goto('/energie');
  await expect(main.getByRole('region', { name: 'Tarieven' })).toBeVisible();
  await expect(main.getByRole('form', { name: 'Tarief toevoegen' })).toHaveCount(0);
  await expect(main.getByRole('button', { name: /^Tarief vanaf .* (wijzigen|verwijderen)$/ })).toHaveCount(0);

  await page.goto('/apparaten');
  await expect(main.getByRole('heading', { name: 'Gekoppelde apparaten' })).toBeVisible();
  await expect(main.getByRole('button', { name: /^Beheren: / })).toHaveCount(0);
  await expect(main.getByRole('combobox', { name: /^Rol van / })).toHaveCount(0);
  await expect(main.getByRole('heading', { name: 'Apparaat toevoegen' })).toHaveCount(0);

  await page.goto('/systeem');
  await expect(main.getByText('Instellingen, back-ups en herstellen zijn alleen beschikbaar voor beheerders.')).toBeVisible();
  await expect(main.getByRole('link', { name: 'Installatiewizard' })).toHaveCount(0);
  await expect(main.getByRole('region', { name: 'Back-ups en bewaren' })).toHaveCount(0);

  // The server checks too: a command from a viewer is refused.
  const csrf = (await page.context().cookies()).find((c) => c.name === 'shc_csrf')?.value ?? '';
  const res = await page.request.post(`/api/v1/zones/${zone.id}/command`, { data: { target_c: 22 }, headers: { 'X-CSRF-Token': decodeURIComponent(csrf) } });
  expect(res.status()).toBe(403);
});
