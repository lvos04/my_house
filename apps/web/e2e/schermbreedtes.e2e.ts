import { expect, expectNoHorizontalScroll, seed, seedAirco, test } from './helpers';

test('Klimaat onderscheidt ontbrekende, oude en verse sensormetingen', async ({ adminPage: page, api, homeId, isMobile, viewport }) => {
  const zone = await api.zone(homeId, 'Woonkamer');
  const pad = `/zones/${zone.id}`;
  const airco = seedAirco(homeId, 'Proefairco');
  const sensor = seed<{ id: string }>('device', {
    home_id: homeId, provider: 'zigbee', kind: 'zigbee_sensor', name: 'Proefsensor',
  });
  const koppeling = { unit_device_id: zone.unit_device_id, sensor_device_id: zone.sensor_device_id };
  await api.send('PATCH', pad, { unit_device_id: airco.id, sensor_device_id: sensor.id });
  try {
    await page.goto('/');
    const menu = page.getByRole('navigation', { name: isMobile ? 'Hoofdmenu (mobiel)' : 'Hoofdmenu', exact: true });
    await expect(menu).toBeVisible();
    const zonekaart = page.getByRole('article', { name: zone.name, exact: true });
    await expect(zonekaart).toBeVisible();
    expect(await page.evaluate(() => window.innerWidth), 'gemeten schermbreedte').toBe(viewport?.width);
    await expectNoHorizontalScroll(page);
    await menu.getByRole('link', { name: 'Klimaat' }).click();
    await page.getByRole('tab', { name: zone.name, exact: true }).click();
    const bediening = page.getByRole('region', { name: 'Bediening' });
    await expect(bediening.getByText('geen meting', { exact: true })).toBeVisible();
    await expect(bediening.getByText(/^nu\s/i)).toHaveCount(0);

    seed('readings', { device_id: sensor.id, metric: 'temperature', unit: '°C', points: [[180, 19.4]] });
    seed('readings', { device_id: sensor.id, metric: 'humidity', unit: '%', points: [[120, 46]] });
    const oudeZone = await api.zone(homeId, zone.name);
    expect(oudeZone.temperature.stale).toBe(true);
    expect(oudeZone.humidity.stale).toBe(true);
    await page.reload();
    await expect(bediening.getByText('Verouderd', { exact: true })).toBeVisible();
    const oudeTemperatuur = bediening.getByText(/laatst bekend.*19,4°/i);
    const oudeVochtigheid = bediening.getByText(/46% RV.*laatst bekend/i);
    await expect(oudeTemperatuur).toBeVisible();
    await expect(oudeTemperatuur.locator('time')).toHaveAttribute('datetime', oudeZone.temperature.at);
    await expect(oudeVochtigheid).toBeVisible();
    await expect(oudeVochtigheid.locator('time')).toHaveAttribute('datetime', oudeZone.humidity.at);
    await expect(bediening.getByText(/^nu\s/i)).toHaveCount(0);
    await expectNoHorizontalScroll(page);
    // Tekstafstand volgens WCAG 1.4.12, met de langste meetlabels in de kaart.
    await page.addStyleTag({ content: `
      * { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }
      p { margin-bottom: 2em !important; }
    ` });
    await expectNoHorizontalScroll(page);

    seed('readings', { device_id: sensor.id, metric: 'temperature', unit: '°C', points: [[0, 20.6]] });
    await page.reload();
    await expect(bediening.getByText('nu 20,6°', { exact: true })).toBeVisible();
    await expect(bediening.getByText(/19,4°/)).toHaveCount(0);
    await expect(oudeVochtigheid).toBeVisible();
    await expectNoHorizontalScroll(page);

    await menu.getByRole('link', { name: 'Overzicht' }).click();
    await expect(zonekaart.getByText('20,6°', { exact: true })).toBeVisible();
    await expect(zonekaart.getByText(/46% RV.*laatst bekend/i)).toBeVisible();
    await expectNoHorizontalScroll(page);
  } finally {
    await api.send('PATCH', pad, koppeling);
  }
});
