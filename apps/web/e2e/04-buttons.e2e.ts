// Flow 4: a Hue switch whose button the bridge already handles (bridge rule) and that has two app
// mappings on the same press (data from an older version). The conflict is shown, "Simuleer" says
// what a press would do without doing it, and removing one mapping leaves only the bridge warning
// (verbeterlijst 25, 26).
import { randomUUID } from 'node:crypto';
import { expect, seed, test, unique } from './helpers';

test('Hue-knop: conflict met bridge-regel en dubbele koppeling, simuleren', async ({ adminPage: page, api, homeId }) => {
  const u = unique();
  const group = seed<{ id: string; name: string }>('device', {
    home_id: homeId,
    provider: 'hue',
    kind: 'light_group',
    name: `Gang ${u}`,
    stable_identity: randomUUID(),
    online: true,
    state: { type: 'grouped_light', on: false, brightness: 0, owner_type: 'room', all_lights: false, lights_on: 0, lights_total: 2, lights_reachable: 2, name: `Gang ${u}` },
  });
  const [b1, b2] = [randomUUID(), randomUUID()];
  const events = ['initial_press', 'short_release', 'long_press'];
  const button = seed<{ id: string; name: string }>('device', {
    home_id: homeId,
    provider: 'hue',
    kind: 'button',
    name: `Schakelaar gang ${u}`,
    stable_identity: randomUUID(),
    model: 'Hue dimmer switch',
    online: true,
    state: {
      type: 'button_device',
      buttons: [
        { id: b1, control_id: 1, type: 'button', events },
        { id: b2, control_id: 2, type: 'button', events },
      ],
      bridge_rules: [b1],
      bridge_rule_names: ['Gang aan/uit'],
      battery: 87,
    },
  });
  const woonkamer = await api.zone(homeId, 'Woonkamer');
  const lightsMapping = { home_id: homeId, device_id: button.id, button: b1, event: 'short_release', mode: 'lights' };
  seed('mapping', { ...lightsMapping, action: { steps: [{ type: 'light', device_id: group.id, action: 'set_group', params: { on: true, brightness: 80 } }] } });
  seed('mapping', { ...lightsMapping, mode: 'climate', action: { steps: [{ type: 'zone', zone_id: woonkamer.id, action: 'resume' }] } });

  await page.goto('/verlichting');
  const card = page.getByRole('article', { name: button.name });
  await expect(card).toBeVisible();
  await expect(card.getByText('Regels op de Hue Bridge zelf: Gang aan/uit.', { exact: false })).toBeVisible();
  const knop1 = card.getByRole('region', { name: 'Knop 1' });
  await expect(knop1.getByText('Ook een regel op de Hue Bridge')).toBeVisible();
  await expect(card.getByRole('region', { name: 'Knop 2' }).getByText('Ook een regel op de Hue Bridge')).toHaveCount(0);

  // Both mappings on "Kort indrukken" are marked as a conflict.
  const row = knop1.getByRole('listitem', { name: 'Knop 1 · Kort indrukken' });
  await expect(row.locator('.lt-conflict')).toHaveCount(2);
  await expect(row.locator('.lt-conflict').first()).toHaveText('Conflict: Meerdere acties op hetzelfde knopevent.');
  await expect(row.getByText(`${group.name}: aan, 80%`, { exact: false })).toBeVisible();
  await expect(row.getByText('Woonkamer: terug naar schema')).toBeVisible();

  // Simulate: what would happen, nothing executed.
  await row.getByRole('button', { name: 'Simuleer Knop 1 · Kort indrukken' }).click();
  const sim = row.getByTestId('simulation');
  await expect(sim).toContainText('Simulatie (er is niets uitgevoerd):');
  await expect(sim).toContainText('Let op: de Hue Bridge voert voor deze knop zelf ook een actie uit.');
  await expect(sim.getByText('Conflict: Meerdere acties op hetzelfde knopevent.')).toHaveCount(2);
  await expect(sim.getByRole('listitem').filter({ hasText: group.name })).toContainText('Wordt uitgevoerd');
  await expect(sim.getByRole('listitem').filter({ hasText: 'Woonkamer: terug naar schema' })).toContainText('Wordt uitgevoerd');
  const lights = await api.get<{ groups: { id: string; last_command: unknown; on: boolean }[] }>(`/homes/${homeId}/lights`);
  expect(lights.groups.find((g) => g.id === group.id)).toMatchObject({ on: false, last_command: null });
  await sim.getByRole('button', { name: 'Sluiten' }).click();
  await expect(sim).toHaveCount(0);

  // An unmapped press: the simulation says nothing is linked (the bridge still acts on its own).
  await knop1.getByRole('button', { name: 'Simuleer Knop 1 · Lang indrukken' }).click();
  await expect(knop1.getByTestId('simulation')).toContainText('Aan deze knopgebeurtenis is in de app niets gekoppeld.');

  // Remove the climate mapping (with confirmation): only the bridge-rule warning stays.
  page.once('dialog', (d) => d.dismiss());
  await row.getByRole('button', { name: 'Koppeling Knop 1 · Kort indrukken verwijderen' }).nth(1).click();
  await expect(row.locator('.lt-conflict')).toHaveCount(2);
  page.once('dialog', (d) => d.accept());
  await row.getByRole('button', { name: 'Koppeling Knop 1 · Kort indrukken verwijderen' }).nth(1).click();
  await expect(row.locator('.lt-conflict')).toHaveCount(1);
  await expect(row.locator('.lt-conflict')).toHaveText(
    'Conflict: De Hue Bridge voert voor deze knop zelf al een actie uit; dubbele bediening mogelijk.',
  );
  await expect(row.getByText('Woonkamer: terug naar schema')).toHaveCount(0);
});
