// Flow 6: back-ups. Set the automatic backup (passphrase, hour, how many, daily dump + weekly full
// export), make a backup now, download it, and check it with "Herstellen en verhuizen" step 1 (upload
// and verify): a wrong passphrase is refused in Dutch, the right one shows what is in the file. Nothing
// is restored. Second test: the push address for messages outside the app (Systeem › Meldingen buiten
// de app): refused in Dutch, set, test message, removed.
import { readFile } from 'node:fs/promises';
import { expect, HOME_NAME, test } from './helpers';

const PASSPHRASE = 'correct paard batterij niet';

test('back-up: instellingen, nu maken, downloaden en controleren via upload', async ({ adminPage: page }, testInfo) => {
  test.setTimeout(180_000);
  await page.goto('/systeem#backups');
  const section = page.getByRole('region', { name: 'Back-ups en bewaren' });
  await expect(section).toBeVisible();

  // Automatic backups: passphrase (twice), hour and number to keep.
  const settings = section.getByRole('form', { name: 'Instellingen automatische back-ups' });
  await settings.getByLabel('Automatische back-up aan').check();
  await settings.getByRole('button', { name: 'Opslaan' }).click();
  await expect(section.getByRole('alert')).toContainText('Stel eerst een wachtwoordzin in');
  await settings.getByLabel(/^Tijdstip/).selectOption('2');
  await settings.getByLabel('Aantal bewaren').fill('7');
  await settings.getByLabel('Wachtwoordzin (min. 12 tekens)').fill(PASSPHRASE);
  await settings.getByLabel('Herhaal wachtwoordzin').fill(PASSPHRASE);
  await settings.getByRole('button', { name: 'Opslaan' }).click();
  await expect(section.getByText('Aan, dagelijks rond 02:00 (Europe/Amsterdam)')).toBeVisible();
  await expect(section.getByText('ingesteld in de app')).toBeVisible();
  await expect(section.getByText('7 automatische')).toBeVisible();
  // The passphrase is never shown again; the field now is for a new one.
  await expect(settings.getByLabel('Nieuwe wachtwoordzin (optioneel)')).toHaveValue('');
  const fact = (term: string) => section.locator('dl.facts div').filter({ has: page.locator('dt', { hasText: new RegExp(`^${term}$`) }) }).locator('dd');
  await expect(fact('Wat')).toHaveText('Elke dag een volledige export');

  // Every day a database dump and weekly the full export (item 32): the first one is a full export.
  await settings.getByLabel('Wat maken').selectOption('dump_daily_full_weekly');
  await settings.getByRole('button', { name: 'Opslaan' }).click();
  await expect(fact('Wat')).toHaveText('Elke dag een database-dump, elke week een volledige export');
  await expect(fact('Volgende')).toContainText('· volledige export');
  await expect(fact('Laatste automatische dump')).toHaveText('nog geen');
  await expect(fact('Bewaard')).toHaveText('7 automatische exports en 7 dumps');
  await expect(settings.getByLabel('Aantal bewaren (per soort)')).toHaveValue('7');
  // Back to a full export every day for the rest of the tests.
  await settings.getByLabel('Wat maken').selectOption('daily_full');
  await settings.getByRole('button', { name: 'Opslaan' }).click();
  await expect(fact('Wat')).toHaveText('Elke dag een volledige export');
  await expect(fact('Laatste automatische dump')).toHaveCount(0);

  // Make a backup now (runs in a separate process on the server).
  const manual = section.getByRole('form', { name: 'Back-up maken' });
  await manual.getByLabel('Wachtwoordzin (min. 12 tekens)').fill(PASSPHRASE);
  await manual.getByRole('button', { name: 'Nu back-up maken' }).click();
  const made = section.getByRole('status').filter({ hasText: 'Back-up gemaakt:' });
  await expect(made).toBeVisible({ timeout: 90_000 });
  await expect(made).toContainText(/\.shcbak · .+ · [\d.]+ records/);

  const list = section.getByRole('table', { name: 'Back-ups, nieuwste eerst' });
  const row = list.getByRole('row').filter({ hasText: 'Handmatig' }).first();
  await expect(row).toContainText('geheimen ja');
  await expect(row).toContainText('Volledige export');
  const link = row.getByRole('link', { name: /^Back-up van .+ downloaden$/ });
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  expect(download.suggestedFilename()).toMatch(/\.shcbak$/);
  const file = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(file);
  const head = (await readFile(file)).subarray(0, 8).toString('latin1');
  expect(head).toBe('SHCBAK2\n');

  // Restore step 1: upload and verify. A wrong passphrase first.
  const restore = page.getByRole('region', { name: 'Herstellen en verhuizen' });
  const upload = restore.getByRole('form', { name: 'Back-up uploaden' });
  await upload.getByLabel('Back-upbestand (.shcbak)').setInputFiles(file);
  await upload.getByLabel('Wachtwoordzin van de back-up').fill('dit is niet de goede zin');
  await upload.getByRole('button', { name: 'Uploaden en controleren' }).click();
  await expect(upload.locator('.note.error')).toBeVisible({ timeout: 30_000 });
  await expect(restore.getByRole('heading', { name: '2 · Controleren en kiezen' })).toHaveCount(0);

  await upload.getByLabel('Wachtwoordzin van de back-up').fill(PASSPHRASE);
  await upload.getByRole('button', { name: 'Uploaden en controleren' }).click();
  await expect(restore.getByRole('heading', { name: '2 · Controleren en kiezen' })).toBeVisible({ timeout: 30_000 });
  const facts = (term: string) => restore.locator('dl.facts div').filter({ has: page.locator('dt', { hasText: new RegExp(`^${term}$`) }) }).locator('dd');
  await expect(facts('Bestand')).toContainText(download.suggestedFilename());
  await expect(facts('Versievergelijking')).toHaveText('Zelfde versie als deze server');
  await expect(facts('Woningen')).toHaveText(HOME_NAME);
  await expect(facts('Records')).toHaveText(/^[\d.]+ in \d+ tabellen$/);
  await expect(facts('Geheimen')).toHaveText('ja');
  await expect(restore.getByRole('radio', { name: /Zelfde woning/ })).toBeChecked();
  await expect(restore.getByRole('button', { name: 'Herstellen' })).toBeDisabled();

  // Cancel: the upload is discarded and nothing changed.
  await restore.getByRole('button', { name: 'Annuleren' }).click();
  await expect(restore.getByRole('form', { name: 'Back-up uploaden' })).toBeVisible();
});

test('meldingen buiten de app: adres weigeren, instellen, testbericht en verwijderen', async ({ adminPage: page }) => {
  await page.goto('/systeem#meldingen');
  const card = page.getByRole('region', { name: 'Meldingen buiten de app' });
  await expect(card.getByText('Uit (geen adres)')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Testbericht sturen' })).toBeDisabled();

  // The server refuses an address with a user name and password, in Dutch, without the field name.
  const input = card.getByLabel('Push-adres', { exact: true });
  await input.fill('https://ik:geheim@ntfy.example/huis');
  await card.getByRole('button', { name: 'Opslaan' }).click();
  await expect(card.getByRole('alert')).toHaveText('Zet geen gebruikersnaam of wachtwoord in het adres; gebruik een geheim onderwerp (topic).');
  await expect(card.getByText('Uit (geen adres)')).toBeVisible();

  // Saved: only scheme and host are shown again (the topic is the secret).
  await input.fill('http://127.0.0.1:9/e2e-geheim-onderwerp');
  await card.getByRole('button', { name: 'Opslaan' }).click();
  await expect(card.getByText('Aan (ingesteld in de app)')).toBeVisible();
  await expect(card.getByText('http://127.0.0.1:9/…')).toBeVisible();
  await expect(card.getByText('e2e-geheim-onderwerp')).toHaveCount(0);
  await expect(card.getByRole('alert')).toHaveCount(0);

  // Nothing listens on that port: the test message says so.
  await card.getByRole('button', { name: 'Testbericht sturen' }).click();
  await expect(card.getByRole('alert')).toContainText('Testbericht niet verstuurd:', { timeout: 20_000 });

  page.once('dialog', (d) => void d.accept());
  await card.getByRole('button', { name: 'Adres verwijderen' }).click();
  await expect(card.getByText('Uit (geen adres)')).toBeVisible();
  await expect(card.getByText('Adres verwijderd.')).toBeVisible();
});
