import { expect, test } from './helpers';
import { axe, contentOutside, outsideCards, smallTargets, smallText } from './peil-metingen';

test('beheerder ziet gevolg en houdt focus in de inschakeldialoog op smalle en brede schermen', async ({ adminPage: page }) => {
  const path = '**/api/v1/commissioning';
  const stamp = '2026-11-18T10:00:00Z';
  let writes = 0;
  let enabled = false;
  await page.route(path, (route) => route.fulfill({
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
      await expect(review.getByRole('checkbox')).not.toBeChecked();
      await opener.click();
      await expect(dialog.getByText(/2 apparaten/)).toBeVisible();
      await expect(dialog.getByRole('button', { name: 'Annuleren' })).toBeFocused();
      await expect(dialog.getByRole('button', { name: 'Beveiliging inschakelen' })).toBeDisabled();
      await page.keyboard.press('Tab');
      await expect(dialog.getByRole('checkbox')).toBeFocused();
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
