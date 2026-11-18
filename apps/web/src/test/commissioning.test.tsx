import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CommissioningPanel } from '../devices/Commissioning';
import { TestHistory } from '../devices/CommissioningActions';
import { COMMISSIONING_TEXT } from '../devices/commissioning-text';
import { InstallPage, INSTALL_KEY } from '../pages/Install';
import { ApiError } from '../api/client';
import { ErrorNote } from '../components/bits';
import { mockApi, Providers, viewer } from './helpers';

const lamp = {
  id: 'lamp', home_id: 'h1', home_name: 'Proefwoning', room_name: 'Proefkamer',
  name: 'Proeflamp', kind: 'light', lifecycle: 'active', compatibility: 'untested',
  protected: false, blocked_when_enabled: true, confirmation_allowed: true,
  bulk_confirmation_allowed: true, tested_at: null, last_confirmed_command: null,
};
const gate = {
  gate: { version: 1, enabled: false, known: true, origin: 'existing_devices', enabled_at: null, enabled_by: null },
  task_required: true, affected_count: 3, untested_count: 3, unsupported_count: 0,
};
const page = { items: [lamp], next_cursor: null, total: 1, observed_at: '2026-11-18T10:00:00Z', gate_etag: '"lijst"' };
const base = '/api/v1/commissioning';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.removeItem(INSTALL_KEY); });

function setup() {
  const calls = mockApi((method, url) => {
    if (method === 'POST') return { body: { items: [lamp] } };
    if (url === base) return { body: gate, headers: { etag: '"gate"' } };
    if (url === base + '/devices/lamp') return { body: lamp, headers: { etag: '"lamp"' } };
    if (url.startsWith(base + '/devices?')) return { body: page };
    return { body: [] };
  });
  render(<Providers><CommissioningPanel homeId="h1" /></Providers>);
  return { calls, user: userEvent.setup() };
}

it('toont de blijvende taak en selecteert niets automatisch', async () => {
  const { calls, user } = setup();
  expect(await screen.findByText(COMMISSIONING_TEXT.taskTitle.nl)).toBeVisible();
  await user.click(screen.getByRole('link', { name: COMMISSIONING_TEXT.review.nl }));
  expect(await screen.findByLabelText('Selecteer Proeflamp')).not.toBeChecked();
  expect(screen.getByText(COMMISSIONING_TEXT.noneConfirmed.nl)).toBeVisible();
  expect(calls.every(c => c.method === 'GET')).toBe(true);
});

it('vereist een bewuste bulkbevestiging en verstuurt alleen de selectie met de ETag', async () => {
  const { calls, user } = setup();
  await user.click(await screen.findByRole('link', { name: COMMISSIONING_TEXT.review.nl }));
  await user.click(await screen.findByLabelText('Selecteer Proeflamp'));
  await user.click(screen.getByRole('button', { name: COMMISSIONING_TEXT.mark.nl }));
  const dialog = screen.getByRole('alertdialog');
  const submit = within(dialog).getByRole('button', { name: '1 apparaat als getest markeren' });
  expect(within(dialog).getByText('Proeflamp')).toBeVisible();
  expect(submit).toBeDisabled();
  expect(calls.some(c => c.method === 'POST')).toBe(false);
  await user.click(within(dialog).getByLabelText(COMMISSIONING_TEXT.bulkCheck.nl));
  await user.click(submit);
  const post = calls.find(c => c.method === 'POST');
  expect(post).toMatchObject({ url: base + '/devices/confirm', body: { device_ids: ['lamp'], confirmed: true } });
  const headers = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.headers;
  expect(new Headers(headers).get('If-Match')).toBe('"lijst"');
  expect(new Headers(headers).get('Idempotency-Key')).toMatch(/^[\da-f-]{36}$/i);
});

it('opent de inschakeldialoog met actuele aantallen voor alle woningen en vereist akkoord', async () => {
  const { calls, user } = setup();
  await user.click(await screen.findByRole('button', { name: COMMISSIONING_TEXT.enable.nl }));
  const dialog = await screen.findByRole('alertdialog');
  expect(within(dialog).getByText(/3 apparaten/)).toBeVisible();
  const submit = within(dialog).getByRole('button', { name: COMMISSIONING_TEXT.enable.nl });
  expect(submit).toBeDisabled();
  expect(calls.some(c => c.method === 'POST')).toBe(false);
  await user.click(within(dialog).getByLabelText(COMMISSIONING_TEXT.enableCheck.nl));
  await user.click(submit);
  await waitFor(() => expect(calls.find(c => c.method === 'POST')).toMatchObject({
    url: base + '/enable', body: { confirmed: true },
  }));
});

it('maakt onbekende configuratie zichtbaar en biedt geen inschakelmutatie', async () => {
  const calls = mockApi((_, url) => url === base
    ? { body: { ...gate, gate: { ...gate.gate, known: false, enabled: true } }, headers: { etag: '"gate"' } }
    : { body: page });
  render(<Providers><CommissioningPanel homeId="h1" /></Providers>);
  expect(await screen.findByText(COMMISSIONING_TEXT.configUnknown.nl)).toBeVisible();
  expect(screen.queryByRole('button', { name: COMMISSIONING_TEXT.enable.nl })).not.toBeInTheDocument();
  expect(calls.some(c => c.method === 'POST')).toBe(false);
});

it('haalt voor een kijker geen beheerinformatie op', async () => {
  const calls = mockApi(() => ({ body: [] }));
  render(<Providers user={viewer}><CommissioningPanel homeId="h1" /><TestHistory id="lamp" name="Proeflamp" /></Providers>);
  expect(screen.queryByText(COMMISSIONING_TEXT.taskTitle.nl)).not.toBeInTheDocument();
  expect(calls.some(c => c.url.startsWith(base))).toBe(false);
});

it('vraagt historie op voor het ene apparaat en vervolgt met de servercursor', async () => {
  const calls = mockApi((_, url) => ({
    body: url.includes('cursor=21')
      ? { items: [{ id: 20, at: page.observed_at, actor: 'Tweede beheerder', model: 'Testmodel', firmware: '2' }], next_cursor: null }
      : { items: [{ id: 21, at: page.observed_at, actor: 'Eerste beheerder', model: null, firmware: null }], next_cursor: 21 },
  }));
  render(<Providers><TestHistory id="lamp" name="Proeflamp" /></Providers>);
  const user = userEvent.setup();
  await user.click(screen.getByText('Testgeschiedenis van Proeflamp'));
  expect(await screen.findByText(/Eerste beheerder/)).toBeVisible();
  expect(screen.getAllByText(COMMISSIONING_TEXT.notRecorded.nl)).toHaveLength(2);
  await user.click(screen.getByRole('button', { name: COMMISSIONING_TEXT.more.nl }));
  expect(await screen.findByText(/Tweede beheerder/)).toBeVisible();
  expect(calls.filter(c => c.url.startsWith(base)).map(c => c.url)).toEqual([
    base + '/devices/lamp/history', base + '/devices/lamp/history?cursor=21',
  ]);
});

it('toont de wizardvolgorde en biedt met oude voortgang geen Gree-koppeling aan', async () => {
  localStorage.setItem(INSTALL_KEY, JSON.stringify({ step: 'gree', done: ['hue', 'gree'] }));
  const calls = mockApi(() => ({ body: [] }));
  render(<Providers><InstallPage /></Providers>);
  const user = userEvent.setup();
  const nav = within(screen.getByRole('navigation', { name: COMMISSIONING_TEXT.wizardNav.nl }));
  const labels = nav.getAllByRole('button').map(button => button.textContent);
  const first = labels.findIndex(label => label?.includes(COMMISSIONING_TEXT.meters.nl));
  expect(first).toBeGreaterThan(-1);
  expect(labels.slice(first + 1, first + 4).join()).toMatch(/Hue uitlezen.*Hue testen en bevestigen.*Gree controleren/);
  expect(screen.getByText(COMMISSIONING_TEXT.d12Wait.nl)).toBeVisible();
  expect(screen.queryByRole('button', { name: /koppelen/i })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: COMMISSIONING_TEXT.greeLater.nl }));
  expect(screen.getByRole('heading', { name: 'Zigbee' })).toHaveFocus();
  expect(calls.some(c => c.method === 'POST')).toBe(false);
});

it('legt een bedienweigering uit en toont de beheerlink alleen aan de beheerder', () => {
  const refusal = new ApiError(409, 'device_not_tested', 'Apparaat ongetest');
  const adminView = render(<Providers><ErrorNote error={refusal} /></Providers>);
  expect(screen.getByRole('alert')).toHaveTextContent(COMMISSIONING_TEXT.denied.nl);
  expect(screen.getByRole('link', { name: COMMISSIONING_TEXT.devices.nl })).toHaveAttribute('href', '/apparaten');
  adminView.unmount();

  render(<Providers user={{ ...viewer, can_control: true }}><ErrorNote error={refusal} /></Providers>);
  expect(screen.getByRole('alert')).toHaveTextContent(COMMISSIONING_TEXT.denied.nl);
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

it('heeft voor de nieuwe teksten een Nederlandse en Engelse vertaling', () => {
  for (const text of Object.values(COMMISSIONING_TEXT)) {
    expect(text.nl.length).toBeGreaterThan(0);
    expect(text.en.length).toBeGreaterThan(0);
  }
});
