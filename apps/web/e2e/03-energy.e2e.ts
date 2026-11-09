// Flow 3: energy. Add a tariff with the optional fields left empty (verbeterlijst 7), step to the
// previous and next day, and download the export (streamed CSV, Dutch columns).
import { readFile } from 'node:fs/promises';
import { expect, test } from './helpers';

/** YYYY-MM-DD in the home's time zone, `offset` days from today. */
function day(offset = 0): string {
  const now = new Date(Date.now() + offset * 86_400_000);
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Amsterdam', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}

test('energie: tarief zonder optionele velden, vorige en volgende dag, export', async ({ adminPage: page, api, homeId }) => {
  for (const t of await api.get<{ id: string }[]>(`/homes/${homeId}/tariffs`)) await api.send('DELETE', `/tariffs/${t.id}`);
  await page.goto('/energie');
  const title = page.getByTestId('energy-title');
  await expect(title).toBeVisible();

  // Tariff: an empty price is refused with a Dutch reason; empty optional fields are fine.
  const form = page.getByRole('form', { name: 'Tarief toevoegen' });
  await expect(form.getByLabel('Geldig vanaf')).toHaveValue(`${day().slice(0, 4)}-01-01`);
  await form.getByRole('button', { name: 'Tarief opslaan' }).click();
  await expect(form.getByText('Vul de stroomprijs per kWh in.')).toBeVisible();
  await form.getByLabel('Stroomprijs € per kWh').fill('0,27');
  await expect(form.getByLabel('Teruglevering € per kWh (optioneel)')).toHaveValue('');
  await expect(form.getByLabel('Vaste kosten € per dag (optioneel)')).toHaveValue('');
  await expect(form.getByLabel('Gasprijs € per m³ (optioneel)')).toHaveValue('');
  await form.getByRole('button', { name: 'Tarief opslaan' }).click();

  const tariffs = page.getByRole('region', { name: 'Tarieven' });
  const row = tariffs.getByRole('row').filter({ hasText: '€ 0,27' });
  await expect(row).toBeVisible();
  await expect(row.getByRole('cell').nth(1)).toHaveText('€ 0,00'); // teruglevering: empty = 0
  await expect(row.getByRole('cell').nth(3)).toHaveText('–'); // no gas price
  const stored = await api.get<any[]>(`/homes/${homeId}/tariffs`);
  expect(stored).toHaveLength(1);
  expect(stored[0]).toMatchObject({ import_per_kwh: 0.27, export_per_kwh: 0, fixed_per_day: 0, gas_per_m3: null });

  // Day view: previous day, then back with "next"; the next button stops at today.
  const nav = page.getByRole('group', { name: 'Periode kiezen' });
  await nav.getByRole('tab', { name: 'Dag' }).click();
  const todayTitle = await title.textContent();
  await expect(page.getByText(/^Energie · Vandaag$/)).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Volgende dag (nog niet begonnen)' })).toBeDisabled();
  await expect(nav.getByLabel('Datum')).toHaveValue(day());

  await nav.getByRole('button', { name: /^Vorige dag: / }).click();
  await expect(page.getByText(/^Energie · Gisteren$/)).toBeVisible();
  await expect(title).not.toHaveText(todayTitle ?? '');
  await expect(nav.getByLabel('Datum')).toHaveValue(day(-1));
  await expect(page.getByRole('region', { name: 'Verbruik per uur' })).toBeVisible();
  // The period tabs are a tab set with a panel: the chosen period's figures, controlled by its tab.
  const panel = page.getByRole('tabpanel', { name: 'Dag' });
  await expect(panel.getByRole('region', { name: 'Verbruik per uur' })).toBeVisible();
  await expect(nav.getByRole('tab', { name: 'Dag' })).toHaveAttribute('aria-controls', (await panel.getAttribute('id')) ?? 'ontbreekt');

  // The export follows the period shown.
  const exportCard = page.getByRole('region', { name: 'Exporteren' });
  await expect(exportCard.getByLabel('Van')).toHaveValue(day(-1));
  await expect(exportCard.getByLabel('Tot en met')).toHaveValue(day(-1));

  await nav.getByRole('button', { name: /^Volgende dag: / }).click();
  await expect(page.getByText(/^Energie · Vandaag$/)).toBeVisible();
  await expect(title).toHaveText(todayTitle ?? '');
  await nav.getByRole('button', { name: /^Vorige dag: / }).click();
  await nav.getByRole('button', { name: 'Vandaag' }).click();
  await expect(nav.getByLabel('Datum')).toHaveValue(day());

  // Export: a real download link to the streamed CSV.
  await nav.getByRole('button', { name: /^Vorige dag: / }).click();
  const link = exportCard.getByRole('link', { name: 'Downloaden' });
  await expect(link).toHaveAttribute('href', `/api/v1/homes/${homeId}/energy/export?format=csv&from=${day(-1)}&to=${day(-1)}&resolution=hour`);
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  const csv = await readFile(await download.path(), 'utf8');
  expect(csv.split(/\r?\n/)[0].replace(/^﻿/, '')).toBe('tijd_utc;tijd_lokaal;rol;apparaat;meting;waarde;eenheid;kwaliteit;opmerking');

  // JSON with day totals.
  await exportCard.getByLabel('Bestand').selectOption('json');
  await exportCard.getByLabel('Detail').selectOption({ label: 'Per dag' });
  const [json] = await Promise.all([page.waitForEvent('download'), link.click()]);
  const body = JSON.parse(await readFile(await json.path(), 'utf8'));
  expect(body).toMatchObject({ schema: 'shc.energy.v2', resolution: 'day', timezone: 'Europe/Amsterdam' });
});
