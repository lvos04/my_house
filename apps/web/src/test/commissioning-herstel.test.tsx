import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { qk } from '../api/keys';
import { CommissioningPanel } from '../devices/Commissioning';
import { ConfirmDevice, TestHistory } from '../devices/CommissioningActions';
import { InstallPage } from '../pages/Install';
import { mockApi, newClient, Providers } from './helpers';

const base = '/api/v1/commissioning';
const device = { id: 'lamp', name: 'Ganglamp', compatibility: 'untested', kind: 'light', provider: 'hue',
  lifecycle: 'active', confirmation_allowed: true, bulk_confirmation_allowed: true, protected: false };
const path = base + '/devices/lamp';
const page = { items: [device], next_cursor: null, gate_etag: '"lijst"', observed_at: '2026-11-18T09:00:00Z' };
const gate = { gate: { known: true, enabled: false }, task_required: true, affected_count: 1, untested_count: 1, unsupported_count: 0 };
const v1 = 'shc.install';
const v2 = 'shc.install.v2';
const submit = () => screen.getByRole('button', { name: 'Als getest markeren' });

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear(); });

it('bindt naam en notitie aan één voorvertoning en vraagt bij een gewijzigde versie opnieuw akkoord', async () => {
  const client = newClient();
  const user = userEvent.setup();
  let changed = false;
  const calls = mockApi((_, url) => url === base ? { body: gate, headers: { etag: '"gate"' } } : ({
    body: changed ? { ...device, name: 'Keukenlamp', protected: true } : device,
    headers: { etag: changed ? '"twee"' : '"een"' },
  }));
  render(<Providers client={client}><ConfirmDevice id="lamp" name="Ganglamp" /></Providers>);
  const open = screen.getByRole('button', { name: 'Getest bevestigen' });
  await user.click(open);
  await waitFor(() => expect(submit()).toBeEnabled());
  await user.type(screen.getByRole('textbox'), 'Gecontroleerd in de gang');
  changed = true;
  await act(async () => { await client.refetchQueries({ queryKey: [...qk.commissioning, 'device', 'lamp'] }); });
  await user.click(submit());
  expect(calls.filter(c => c.method === 'POST')).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: 'Annuleren' }));
  await user.click(open);
  expect(await screen.findByRole('heading', { name: 'Keukenlamp als getest markeren?' })).toBeVisible();
  expect(screen.getByRole('textbox')).toHaveValue('');
  await waitFor(() => expect(submit()).toBeEnabled());
  await user.click(submit());
  const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(new Headers(posts[0][1]?.headers).get('If-Match')).toBe('"twee"');
});

it('blokkeert de open bevestiging zodra de installatiebeveiliging onbekend wordt', async () => {
  const client = newClient();
  const user = userEvent.setup();
  let known = true;
  const calls = mockApi((_, url) => url === base ? { body: { ...gate, gate: { ...gate.gate, known } }, headers: { etag: '"gate"' } } :
    url === path ? { body: device, headers: { etag: '"lamp"' } } : { body: page });
  render(<Providers client={client} route="/apparaten#teststatus-apparaten"><CommissioningPanel homeId="h1" /></Providers>);
  await user.click(await screen.findByRole('button', { name: 'Getest bevestigen: Ganglamp' }));
  await waitFor(() => expect(submit()).toBeEnabled());
  known = false;
  await act(async () => { await client.refetchQueries({ queryKey: qk.commissioning, exact: true }); });
  expect(await screen.findByText(/De beveiligingsinstelling is onbekend/)).toBeVisible();
  expect(submit()).toBeDisabled();
  await user.click(submit());
  expect(calls.filter(c => c.method === 'POST')).toHaveLength(0);
});

it.each([401, 403])('schermt beheerdata af na %s, ook bij volgende uitval, tot opnieuw toegang is verleend', async status => {
  const client = newClient();
  let response = 200;
  mockApi((_, url) => {
    if (response !== 200) return { status: response, body: { code: 'forbidden', message: 'Alleen voor een beheerder.' } };
    if (url === base) return { body: gate, headers: { etag: '"gate"' } };
    if (url === path + '/history') return { body: { items: [{ id: 1, at: page.observed_at, actor: 'Proefuitvoerder' }], next_cursor: null } };
    return { body: page };
  });
  render(<Providers client={client} route="/apparaten#teststatus-apparaten">
    <CommissioningPanel homeId="h1" /><TestHistory id="lamp" name="Ganglamp" />
  </Providers>);
  await userEvent.click(screen.getByText('Testgeschiedenis van Ganglamp'));
  expect(await screen.findByText(/Proefuitvoerder/)).toBeVisible();
  expect(await screen.findByLabelText('Selecteer Ganglamp')).toBeVisible();
  const task = 'Bevestig de bestaande apparaten';
  expect(screen.getByText(task)).toBeVisible();
  for (response of [status, 503]) {
    await act(async () => { await client.refetchQueries({ queryKey: qk.commissioning }); });
    await waitFor(() => {
      expect(screen.queryByText(/Proefuitvoerder/)).toBeNull();
      expect(screen.queryByLabelText('Selecteer Ganglamp')).toBeNull();
      expect(screen.queryByText(task)).toBeNull();
    });
  }
  response = 200;
  await act(async () => { await client.refetchQueries({ queryKey: qk.commissioning }); });
  expect(await screen.findByText(/Proefuitvoerder/)).toBeVisible();
  expect(await screen.findByLabelText('Selecteer Ganglamp')).toBeVisible();
  expect(screen.getByText(task)).toBeVisible();
});

it('eist per Hue-lamp de laatste geslaagde leestest en daarna een bewuste bevestiging', async () => {
  localStorage.setItem(v1, JSON.stringify({ version: 2, step: 'hue-testen', done: [] }));
  const user = userEvent.setup();
  let ok = false;
  let readId = 'lamp';
  const calls = mockApi((_, url) => {
    if (url === base) return { body: gate, headers: { etag: '"gate"' } };
    if (url.includes('/homes/h1/devices')) return { body: [device, { ...device, id: 'lamp2', name: 'Hal' }] };
    if (url.endsWith('/test')) return { body: { device_id: readId, ok, checked_at: page.observed_at, checks: [], readings: {}, note: 'Uitgelezen' } };
    return { body: device, headers: { etag: '"lamp"' } };
  });
  render(<Providers><InstallPage /></Providers>);
  const mark = await screen.findByRole('button', { name: 'Getest bevestigen: Ganglamp' });
  const read = screen.getByRole('button', { name: 'Leestest uitvoeren: Ganglamp' });
  expect(mark).toBeDisabled();
  await user.click(read);
  expect(await screen.findByText(/Test vond een probleem/)).toBeVisible();
  expect(mark).toBeDisabled();
  ok = true;
  await user.click(read);
  expect(await screen.findByText(/Leestest uitgevoerd/)).toBeVisible();
  expect(mark).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Getest bevestigen: Hal' })).toBeDisabled();
  readId = 'lamp2';
  await user.click(read);
  await waitFor(() => expect(mark).toBeDisabled());
  readId = 'lamp';
  await user.click(read);
  await waitFor(() => expect(mark).toBeEnabled());
  await user.click(mark);
  await waitFor(() => expect(submit()).toBeEnabled());
  expect(calls.filter(c => c.url.endsWith('/confirm'))).toHaveLength(0);
  await user.click(submit());
  expect(calls.filter(c => c.method === 'POST').map(c => c.url)).toEqual([...Array(4).fill('/api/v1/devices/lamp/test'), path + '/confirm']);
});

it('begrenst een stilvallende bevestiging en herleest vóór een volgende beslissing', async () => {
  let applied = false;
  const calls = mockApi((_, url) => url === base ? { body: gate, headers: { etag: '"gate"' } } : {
    body: applied ? { ...device, compatibility: 'tested', tested_at: page.observed_at } : device,
    headers: { etag: applied ? '"na"' : '"voor"' },
  });
  const get = vi.mocked(fetch).getMockImplementation()!;
  let writes = 0;
  let signal: AbortSignal | null | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (init?.method !== 'POST') return get(url, init);
    writes++;
    signal = init.signal;
    return new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('Afgebroken', 'AbortError')), { once: true });
    });
  });
  render(<Providers><ConfirmDevice id="lamp" name="Ganglamp" /></Providers>);
  await userEvent.click(screen.getByRole('button', { name: 'Getest bevestigen' }));
  await waitFor(() => expect(submit()).toBeEnabled());
  vi.useFakeTimers();
  await act(async () => { fireEvent.click(submit()); });
  expect(writes).toBe(1);
  expect(signal).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(14_999); });
  expect(signal?.aborted).toBe(false);
  expect(screen.getByRole('button', { name: 'Sluiten' })).toBeEnabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(signal?.aborted).toBe(true);
  // Laat ook de ingeplande kennisgeving van de mutatie aan React uitvoeren.
  await act(async () => { await vi.runOnlyPendingTimersAsync(); });
  vi.useRealTimers();
  expect(await screen.findByRole('alert')).toHaveTextContent('Haal de toestand opnieuw op');
  expect(screen.getByRole('button', { name: 'Annuleren' })).toBeEnabled();
  expect(submit()).toBeDisabled();
  applied = true;
  const reads = calls.length;
  await userEvent.click(screen.getByRole('button', { name: 'Opnieuw proberen' }));
  await waitFor(() => expect(calls.length).toBeGreaterThan(reads));
  expect(await screen.findByText(/Getest in dit huis/)).toBeVisible();
  expect(writes).toBe(1);
});

it('behoudt het oorspronkelijke v1-record en hervat beide versies onafhankelijk', async () => {
  mockApi(() => ({ body: [] }));
  const legacy = JSON.stringify({ step: 'hue', done: ['controle', 'homewizard', 'meters', 'hue', 'gree'] });
  localStorage.setItem(v1, legacy);
  const first = render(<Providers><InstallPage /></Providers>);
  expect(screen.getByRole('heading', { name: 'Hue uitlezen' })).toBeVisible();
  expect(localStorage.getItem(v1)).toBe(legacy);
  expect(JSON.parse(localStorage.getItem(v2)!)).toEqual({ step: 'hue-lezen', done: ['controle', 'meters', 'hue-lezen'], version: 2 });
  await userEvent.click(within(screen.getByRole('navigation')).getByRole('button', { name: /Zigbee/ }));
  first.unmount();
  render(<Providers><InstallPage /></Providers>);
  expect(screen.getByRole('heading', { name: 'Zigbee' })).toBeVisible();
  expect(localStorage.getItem(v1)).toBe(legacy);
});

it('maakt een door de eerste v2-uitgave overschreven hervatpositie weer leesbaar voor v1', () => {
  const calls = mockApi(() => ({ body: [] }));
  localStorage.setItem(v1, JSON.stringify({ version: 2, step: 'hue-testen', done: ['meters', 'hue-lezen', 'gree'] }));
  render(<Providers><InstallPage /></Providers>);
  expect(JSON.parse(localStorage.getItem(v1)!)).toEqual({ step: 'hue', done: ['meters', 'hue'] });
  expect(JSON.parse(localStorage.getItem(v2)!)).toEqual({ version: 2, step: 'hue-testen', done: ['meters', 'hue-lezen'] });
  expect(calls.filter(c => c.method !== 'GET')).toHaveLength(0);
});
