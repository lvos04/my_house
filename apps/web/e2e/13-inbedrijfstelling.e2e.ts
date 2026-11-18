import { expect, test } from './helpers';
import { axe, contentOutside, outsideCards, smallTargets, smallText } from './peil-metingen';

test('beheerder ziet gevolg en houdt focus in de inschakeldialoog op smalle en brede schermen', async ({ adminPage: page }) => {
  const path = '**/api/v1/commissioning';
  const stamp = '2026-11-18T10:00:00Z';
  let writes = 0;
  let enabled = false;
  let readFailed = false;
  await page.route(path, (route) => readFailed ? route.fulfill({ status: 503, json: { code: 'dependency_unavailable', message: 'Tijdelijk onbeschikbaar' } }) : route.fulfill({
    headers: { etag: '"proefstand"' },
    json: {
      gate: { known: true, enabled, enabled_at: enabled ? stamp : null },
      untested_count: 2, unsupported_count: 0, affected_count: 2, task_required: !enabled,
    },
  }));
  await page.route(path + '/devices?*', (route) => route.fulfill({
    json: {
      items: [{
        id: 'proeflamp', name: 'Een proeflamp met een lange herkenbare naam voor smalle schermen',
        home_name: 'Proefwoning', room_name: 'Proefkamer', compatibility: 'untested',
        protected: false, confirmation_allowed: true, bulk_confirmation_allowed: true,
        last_confirmed_command: { action: 'set_light', confirmed_at: stamp },
      }],
      next_cursor: null, total: 1, observed_at: stamp, gate_etag: '"proefstand"',
    },
  }));
  await page.route(path + '/enable', async (route) => {
    writes++;
    expect(route.request().headers()['if-match']).toBe('"proefstand"');
    expect(route.request().postDataJSON()).toEqual({ confirmed: true });
    // De eerste wijziging ziet een verouderde toestand en mag niet vanzelf worden herhaald.
    if (writes === 1) {
      await route.fulfill({ status: 412, json: { detail: { code: 'precondition_failed', message: 'De toestand is gewijzigd.' } } });
    } else {
      enabled = true;
      await route.fulfill({ json: {} });
    }
  });

  const opener = page.getByRole('button', { name: 'Beveiliging inschakelen', exact: true });
  const dialog = page.getByRole('alertdialog');
  const review = page.getByRole('region', { name: 'Teststatus van apparaten', exact: true }).last();
  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme });
    for (const width of [320, 360, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto('/apparaten#teststatus-apparaten');
      await expect(review.getByText('Lamp instellen', { exact: false })).toBeVisible();
      const selection = review.getByRole('checkbox');
      await expect(selection).not.toBeChecked();
      await expect(selection).toHaveCSS('width', '20px');
      await expect(selection).toHaveCSS('height', '20px');
      const row = review.locator('tbody tr').first();
      const action = row.getByRole('button', { name: /Getest bevestigen: Een proeflamp/ });
      await expect(action).toHaveText('Getest bevestigen');
      if (width >= 768) {
        await expect(review.getByRole('columnheader', { name: 'Laatste bevestigde bediening' })).toBeVisible();
      } else {
        await expect(row.locator('dt').filter({ hasText: 'Laatste bevestigde bediening' })).toBeVisible();
      }
      await opener.click();
      await expect(dialog.getByText(/2 apparaten/)).toBeVisible();
      await expect(dialog.getByRole('button', { name: 'Annuleren' })).toBeFocused();
      await expect(dialog.getByRole('button', { name: 'Beveiliging inschakelen' })).toBeDisabled();
      await page.keyboard.press('Tab');
      await expect(dialog.getByRole('checkbox')).toBeFocused();
      await expect(dialog.getByRole('checkbox')).toHaveCSS('width', '20px');
      await page.keyboard.press('Shift+Tab');
      await expect(dialog.getByRole('button', { name: 'Annuleren' })).toBeFocused();
      await axe(page, `inschakeldialoog ${colorScheme}, ${width} px`);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
      expect(await outsideCards(page)).toEqual([]);
      expect(await contentOutside(page)).toEqual([]);
      expect(await smallTargets(page)).toEqual([]);
      expect(await smallText(page)).toEqual([]);
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(opener).toBeFocused();
    }
  }
  expect(writes).toBe(0);

  readFailed = true;
  await opener.click();
  const stale = page.getByRole('status').filter({ hasText: 'Verouderd · laatst bekende stand van' });
  await expect(stale).toHaveCSS('border-top-style', 'dashed');
  await expect(stale).toHaveCSS('background-image', /repeating-linear-gradient/);
  await expect(opener).toBeDisabled();
  readFailed = false;
  await page.getByRole('button', { name: 'Opnieuw proberen' }).click();
  await expect(stale).toHaveCount(0);
  await opener.click();
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Beveiliging inschakelen' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'De wijziging kon niet worden bevestigd.' })).toBeVisible();
  await expect(opener).toBeDisabled();
  expect(writes).toBe(1);
  await page.getByRole('button', { name: 'Opnieuw proberen' }).click();

  await opener.click();
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Beveiliging inschakelen' }).click();
  await expect(page.getByText('De beveiliging is ingeschakeld.', { exact: true })).toBeVisible();
  await expect(opener).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(/Beveiliging voor ongeteste apparaten staat aan sinds/)).toBeVisible();
  expect(writes).toBe(2);
});

test('wizard toont de actieve stap zonder uitloop en benoemt de voorwaarde voor Gree', async ({ adminPage: page }) => {
  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme });
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto('/installatie');
    const nav = page.getByRole('navigation', { name: 'Stappen van de installatie' });
    const hue = nav.getByRole('button', { name: 'Hue testen en bevestigen' });
    await hue.focus();
    await page.keyboard.press('Enter');
    const next = page.getByRole('button', { name: 'Volgende: Gree controleren' });
    await next.click();
    await expect(page.getByRole('heading', { name: 'Gree controleren' })).toBeFocused();
    const gate = page.getByRole('status').filter({ hasText: 'Gree-bediening wacht' });
    await expect(gate).toHaveClass(/melding-gate/);
    await expect(gate.locator('svg')).toBeVisible();
    await page.addStyleTag({ content: '* { letter-spacing: .12em; word-spacing: .16em; }' });
    for (const width of [320, 360, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      expect(await nav.evaluate(el => el.scrollWidth - el.clientWidth)).toBe(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
      const current = nav.locator('button[aria-current="step"]');
      await current.scrollIntoViewIfNeeded();
      await expect(current).toBeInViewport({ ratio: 1 });
    }
  }
});
