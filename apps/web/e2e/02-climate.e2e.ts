// Flow 2: link a (fake) airco to a zone, edit the week schedule with a template and "dag kopiëren",
// save it and see the next change; then switch the airco off: +/−, fan and "Handmatig" are disabled
// with "Kies eerst een modus" and "Terug naar schema" is offered (verbeterlijst 11, 12, 13, 31).
import { expect, seedAirco, test, unique } from './helpers';

test('klimaat: airco koppelen, weekschema met sjabloon en dag kopiëren, uitzetten', async ({ adminPage: page, api, homeId }) => {
  const zone = await api.zone(homeId, 'Woonkamer');
  const unit = seedAirco(homeId, `Airco woonkamer ${unique()}`);

  // Not linked yet: neutral text, never an error.
  await page.goto(`/klimaat?zone=${zone.id}&tab=bediening`);
  const control = page.getByRole('region', { name: 'Bediening' });
  await expect(control.getByText('Nog geen airco gekoppeld', { exact: true })).toBeVisible();
  await expect(control.locator('.note.error, .error-text')).toHaveCount(0);

  // Link the unit (Instellingen › Zone-koppeling).
  await control.getByRole('button', { name: 'Unit kiezen voor deze zone' }).click();
  await expect(page).toHaveURL(/tab=instellingen/);
  const link = page.getByRole('region', { name: 'Zone-koppeling' });
  await link.getByLabel('Airco-unit').selectOption({ label: unit.name });
  await link.getByRole('button', { name: 'Opslaan' }).click();
  await expect(link.getByRole('status')).toHaveText('Koppeling opgeslagen.');

  // Week schedule: template "Nachtverlaging", change Monday, copy Monday to the other workdays.
  await page.getByRole('tablist', { name: 'Onderdelen van Woonkamer' }).getByRole('tab', { name: 'Weekschema' }).click();
  const week = page.getByRole('region', { name: 'Weekschema Woonkamer' });
  const template = week.getByRole('group', { name: 'Sjabloon' });
  await template.getByLabel('Sjabloon kiezen').selectOption({ label: 'Nachtverlaging' });
  await template.getByLabel('Comfort °C').fill('21');
  await template.getByLabel('Lager °C').fill('18');
  await template.getByLabel('Modus in sjabloon').selectOption({ label: 'Houd huidige modus' });
  await template.getByRole('button', { name: 'Sjabloon toepassen' }).click();
  await expect(template.getByText('Sjabloon Nachtverlaging ingevuld.', { exact: false })).toBeVisible();
  await expect(week.getByText('Niet opgeslagen wijzigingen.')).toBeVisible();

  const days = week.getByRole('tablist', { name: 'Dag van de week' });
  await days.getByRole('tab', { name: /^maandag/ }).click();
  const monday = week.getByRole('list', { name: 'Blokken op maandag' });
  await expect(monday.getByRole('listitem')).toHaveCount(2);
  await monday.getByLabel('Begintijd blok 1').fill('06:30');
  await monday.getByLabel('Doel °C blok 1').fill('22');

  const copy = week.getByRole('group', { name: 'Dag kopiëren' });
  await copy.getByRole('button', { name: 'Werkdagen' }).click();
  await expect(copy.getByRole('checkbox', { name: 'Vrijdag' })).toBeChecked();
  await expect(copy.getByRole('checkbox', { name: 'Zaterdag' })).not.toBeChecked();
  await copy.getByRole('button', { name: 'Kopiëren naar 4 dagen' }).click();
  await expect(copy.getByText('Maandag gekopieerd naar dinsdag, woensdag, donderdag, vrijdag.')).toBeVisible();

  await days.getByRole('tab', { name: /^woensdag/ }).click();
  const wednesday = week.getByRole('list', { name: 'Blokken op woensdag' });
  await expect(wednesday.getByLabel('Begintijd blok 1')).toHaveValue('06:30');
  await expect(wednesday.getByLabel('Doel °C blok 1')).toHaveValue('22');
  await days.getByRole('tab', { name: /^zaterdag/ }).click();
  await expect(week.getByRole('list', { name: 'Blokken op zaterdag' }).getByLabel('Begintijd blok 1')).toHaveValue('07:00');

  // Preview of the next change before saving, then save.
  await expect(week.getByText('Volgende wijziging:')).toBeVisible();
  await week.getByRole('button', { name: 'Weekschema opslaan' }).click();
  await expect(week.getByText('Opgeslagen.', { exact: true })).toBeVisible();

  const saved = await api.get<{ blocks: { days: number[]; start: string; target_c: number; mode: string | null; label: string }[] }>(`/zones/${zone.id}/schedule`);
  const blocks = saved.blocks.map((b) => ({ days: [...b.days].sort(), start: b.start, target_c: b.target_c, mode: b.mode ?? null, label: b.label }));
  expect(blocks).toEqual(
    expect.arrayContaining([
      { days: [0, 1, 2, 3, 4], start: '06:30', target_c: 22, mode: null, label: 'Dag' },
      { days: [5, 6], start: '07:00', target_c: 21, mode: null, label: 'Dag' },
      { days: [0, 1, 2, 3, 4, 5, 6], start: '22:30', target_c: 18, mode: null, label: 'Nacht' },
    ]),
  );
  expect(blocks).toHaveLength(3);

  // The server's next change shows on the control tab.
  await page.getByRole('tablist', { name: 'Onderdelen van Woonkamer' }).getByRole('tab', { name: 'Bediening' }).click();
  const today = page.getByRole('region', { name: 'Schema vandaag' });
  await expect(today.getByText(/^Volgende: \d{2}:\d{2} · (Dag|Nacht) \d+(,\d)?°/)).toBeVisible();

  // Airco on: the controls work.
  await expect(control.getByRole('button', { name: 'Doeltemperatuur hoger' })).toBeEnabled();
  await expect(control.getByRole('button', { name: 'Handmatig 2 uur' })).toBeEnabled();
  await expect(control.getByRole('button', { name: 'Terug naar schema' })).toHaveCount(0);

  // Switch it off: only a mode choice can start it again, and the way back to the schedule is offered.
  await control.getByRole('group', { name: 'Modus' }).getByRole('button', { name: 'Uit', exact: true }).click();
  await expect(control.getByText('Uitgezet, schema gepauzeerd')).toBeVisible();
  await expect(control.getByRole('button', { name: 'Doeltemperatuur hoger' })).toBeDisabled();
  await expect(control.getByRole('button', { name: 'Doeltemperatuur lager' })).toBeDisabled();
  await expect(control.getByRole('button', { name: 'Handmatig 2 uur' })).toBeDisabled();
  await expect(control.getByRole('group', { name: 'Ventilator' }).getByRole('button', { name: 'Automatisch' })).toBeDisabled();
  await expect(control.getByText(/^Kies eerst een modus\./)).toBeVisible();
  await expect(control.getByRole('group', { name: 'Modus' }).getByRole('button', { name: 'Verwarmen' })).toBeEnabled();
  const resume = control.getByRole('button', { name: 'Terug naar schema' });
  await expect(resume).toBeVisible();

  // After a reload the state comes from the server, not from this tab.
  await page.reload();
  await expect(page.getByRole('region', { name: 'Bediening' }).getByRole('button', { name: 'Terug naar schema' })).toBeVisible();
  await page.getByRole('region', { name: 'Bediening' }).getByRole('button', { name: 'Terug naar schema' }).click();
  await expect(page.getByRole('region', { name: 'Bediening' }).getByRole('button', { name: 'Terug naar schema' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Bediening' }).getByText(/^Volgt het schema/)).toBeVisible();
});
