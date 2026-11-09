// Flow 1: first start. Make the admin, create the home (never a dead end), set the home address,
// and take the first steps of the installation wizard. Runs once on an empty database; every other
// test depends on it (project "onboarding" in playwright.config.ts).
import { ADMIN, expect, HOME_NAME, LAN_URL, test } from './helpers';

test('eerste start: beheerder, woning, thuisadres en installatiewizard', async ({ page }) => {
  await page.goto('/');

  // 1. No default password: the first screen makes the admin account.
  await expect(page.getByRole('heading', { name: 'Beheerder aanmaken' })).toBeVisible();
  await page.getByLabel('Gebruikersnaam').fill(ADMIN.username);
  await page.getByLabel('Wachtwoord', { exact: true }).fill(ADMIN.password);
  await page.getByLabel('Herhaal wachtwoord').fill(`${ADMIN.password}!`);
  await page.getByRole('button', { name: 'Account aanmaken' }).click();
  await expect(page.getByRole('alert')).toHaveText('De wachtwoorden komen niet overeen.');
  await page.getByLabel('Wachtwoord', { exact: true }).fill(ADMIN.password);
  await page.getByLabel('Herhaal wachtwoord').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Account aanmaken' }).click();

  // 2. No home yet: every page offers the form, plus the wizard and restore as a way out.
  const main = page.getByRole('main');
  await expect(main.getByRole('heading', { name: 'Woning aanmaken', level: 1 })).toBeVisible();
  await expect(main.getByRole('link', { name: 'Open de installatiewizard' })).toBeVisible();
  await expect(main.getByRole('link', { name: 'Systeem › Herstellen en verhuizen' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Hoofdmenu', exact: true }).getByRole('link', { name: 'Klimaat' }).click();
  await expect(main.getByRole('heading', { name: 'Woning aanmaken', level: 1 })).toBeVisible();
  await expect(page.getByLabel('Tijdzone')).toHaveValue('Europe/Amsterdam');
  await page.getByLabel('Naam van de woning').fill(HOME_NAME);
  await page.getByRole('button', { name: 'Woning aanmaken' }).click();

  // The new home's default zones appear right away; "Volgende stappen" says what is next.
  const zoneTabs = main.getByRole('tablist', { name: 'Zone' });
  await expect(zoneTabs.getByRole('tab', { name: 'Woonkamer' })).toBeVisible();
  await expect(zoneTabs.getByRole('tab', { name: 'Slaapkamer' })).toBeVisible();
  const next = page.getByRole('region', { name: 'Volgende stappen' });
  await expect(next).toBeVisible();

  // 3. Home address (needed to pair phones), via the first next step.
  await next.getByRole('link', { name: 'Stel het thuisadres in' }).click();
  await expect(page).toHaveURL(/\/systeem#adressen$/);
  const addresses = page.getByRole('form', { name: 'Adressen' });
  await addresses.getByLabel('Thuisadres').fill('http://127.0.0.1:8088');
  await addresses.getByRole('button', { name: 'Opslaan' }).click();
  // Loopback is refused with a Dutch reason; a private address is accepted.
  await expect(page.locator('#adressen .note.error')).toBeVisible();
  await addresses.getByLabel('Thuisadres').fill(LAN_URL);
  await addresses.getByRole('button', { name: 'Opslaan' }).click();
  await expect(page.locator('#adressen').getByRole('status')).toHaveText('Opgeslagen.');
  await page.reload();
  await expect(page.getByRole('form', { name: 'Adressen' }).getByLabel('Thuisadres')).toHaveValue(LAN_URL);

  // 4. Installation wizard: checks, home and address are already there; the brand steps are optional.
  await page.getByRole('link', { name: 'Installatiewizard', exact: true }).click();
  await expect(page).toHaveURL(/\/installatie$/);
  const wizard = page.getByRole('region', { name: 'Controles vooraf' });
  await expect(wizard).toBeVisible();
  await expect(wizard.getByText('Stap 1 van 10')).toBeVisible();
  // The server's checks (mDNS takes a moment); the address set above counts as done.
  await expect(wizard.getByTestId('check-lan_url').locator('.state-line')).toHaveClass(/\bok\b/, { timeout: 20_000 });
  await expect(wizard.getByTestId('check-home').locator('.state-line')).toHaveClass(/\bok\b/);
  await wizard.getByRole('button', { name: 'Volgende', exact: true }).click();

  const homeStep = page.getByRole('region', { name: 'Woning' });
  await expect(homeStep.getByText(`Je woning: ${HOME_NAME} (Europe/Amsterdam). Je kunt doorgaan.`)).toBeVisible();
  await homeStep.getByRole('button', { name: 'Volgende', exact: true }).click();

  const addrStep = page.getByRole('region', { name: 'Adressen' }).first();
  await expect(addrStep.getByLabel('Thuisadres')).toHaveValue(LAN_URL);
  await addrStep.getByRole('button', { name: 'Volgende', exact: true }).click();

  const hue = page.getByRole('region', { name: 'Philips Hue' });
  await expect(hue.getByText('Stap 4 van 10 · optioneel')).toBeVisible();
  await expect(hue.getByRole('button', { name: 'Volgende (of overslaan)' })).toBeVisible();
  // Finished steps are marked in the step list, and progress survives a reload.
  const steps = page.getByRole('navigation', { name: 'Stappen' });
  await expect(steps.getByRole('button', { name: 'Controles vooraf (gedaan)' })).toBeVisible();
  await expect(steps.getByRole('button', { name: 'Woning (gedaan)' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('region', { name: 'Philips Hue' })).toBeVisible();
});
