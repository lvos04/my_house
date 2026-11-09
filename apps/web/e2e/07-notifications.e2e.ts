// Flow 7: a notification raised on the server appears on the open overview without reloading, and
// disappears again when it is resolved (alert.created / alert.resolved over the WebSocket, published
// through PostgreSQL NOTIFY exactly like the worker does). "Gezien" acknowledges it.
import { expect, seed, test, unique } from './helpers';

test('melding verschijnt live en verdwijnt na oplossen', async ({ adminPage: page, homeId }) => {
  const key = `e2e:${unique()}`;
  const title = `Testmelding ${key.slice(4)}`;

  const ws = page.waitForEvent('websocket', (w) => w.url().includes('/api/v1/ws'));
  await page.goto('/');
  const socket = await ws;
  await socket.waitForEvent('framereceived', { predicate: (f) => String(f.payload).includes('"hello"') });
  const alerts = page.getByRole('region', { name: 'Meldingen' });
  await expect(alerts).toBeVisible();
  await expect(alerts.getByText(title)).toHaveCount(0);

  // Raised on the server: arrives over the live connection (the page polls only every 60 s).
  const created = socket.waitForEvent('framereceived', { predicate: (f) => String(f.payload).includes('"alert.created"') && String(f.payload).includes(key.slice(4)) });
  seed('alert', { home_id: homeId, key, severity: 'warn', title, detail: 'Gemaakt door de browsertest.' });
  await created;
  await expect(alerts.getByText(title)).toBeVisible({ timeout: 5_000 });
  await expect(alerts.getByText('Gemaakt door de browsertest.', { exact: false })).toBeVisible();

  // "Gezien" acknowledges it (it stays open until resolved).
  await alerts.getByRole('button', { name: `Melding ${title} afhandelen` }).click();
  await expect(alerts.getByRole('button', { name: `Melding ${title} afhandelen` })).toHaveCount(0);
  await expect(alerts.getByText(title)).toBeVisible();

  // Resolved on the server: removed live.
  const resolved = socket.waitForEvent('framereceived', { predicate: (f) => String(f.payload).includes('"alert.resolved"') });
  seed('resolve', { key });
  await resolved;
  await expect(alerts.getByText(title)).toHaveCount(0, { timeout: 5_000 });
});

// Item 45: the climate page warns that a room does not respond only while the watchdog alert of that
// zone is open (key "watchdog:{zone_id}", raised by the worker), not merely because a watchdog window
// runs (zone.watchdog_since), and the warning goes away live when the alert is resolved.
test('waakhond: waarschuwing bij de zone alleen zolang de waakhondmelding open is', async ({ adminPage: page, api, homeId }) => {
  const zone = await api.zone(homeId, 'Woonkamer');
  const key = `watchdog:${zone.id}`;
  seed('resolve', { key }); // a rerun against the same database starts clean

  const ws = page.waitForEvent('websocket', (w) => w.url().includes('/api/v1/ws'));
  const alertsLoaded = page.waitForResponse((r) => r.url().endsWith(`/api/v1/homes/${homeId}/alerts`) && r.ok());
  await page.goto(`/klimaat?zone=${zone.id}&tab=bediening`);
  const socket = await ws;
  await socket.waitForEvent('framereceived', { predicate: (f) => String(f.payload).includes('"hello"') });
  await alertsLoaded;
  const today = page.getByRole('region', { name: 'Schema vandaag' });
  await expect(today).toBeVisible();
  await expect(today.getByTestId('watchdog-note')).toHaveCount(0);

  const detail = 'De airco verwarmt al 60 minuten, maar de temperatuur ging van 18.0 naar 18.1 °C (doel 21.0 °C).';
  seed('alert', { home_id: homeId, key, severity: 'warn', title: `${zone.name}: temperatuur reageert niet`, detail, source: 'climate' });
  const note = today.getByTestId('watchdog-note');
  await expect(note).toBeVisible({ timeout: 5_000 });
  await expect(note).toContainText(`Waakhond: ${zone.name} reageert niet op de airco`);
  await expect(note).toContainText('ging van 18.0 naar 18.1 °C');

  seed('resolve', { key });
  await expect(today.getByTestId('watchdog-note')).toHaveCount(0, { timeout: 5_000 });
});
