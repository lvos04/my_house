import { afterEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { SystemPage } from '../pages/System';
import { UserContext } from '../user';
import { admin, mockApi, newClient, Providers, viewer } from './helpers';

const title = 'Koppeling met de Hub (bediening)';
const path = '/system/hub-keys';
const old = { state: 'pinned', fingerprint: '01AZ-1110-0101-0000', sequence: '2' };
const next = { fingerprint: '0A1B-2C3D-4E5F-6G7H', sequence: '3' };
const headers = { etag: '"proefversie"' };

afterEach(() => vi.unstubAllGlobals());

function open(state: object) {
  const calls = mockApi((method, url) => {
    if (url.endsWith(path) && method === 'GET') return { body: state, headers };
    if (url.endsWith(path + '/verify')) return { body: next };
    if (url.endsWith(path) && method === 'PUT') return { body: { ...next, state: 'pinned' }, headers };
    return undefined;
  });
  render(<Providers route="/systeem"><SystemPage /></Providers>);
  const card = within(screen.getByRole('region', { name: title }));
  return { card, calls, user: userEvent.setup() };
}

it('weigert een afwijkende vingerafdruk bij het veld en wist beide velden na vastleggen', async () => {
  const { card, calls, user } = open({ state: 'empty' });
  const source = await card.findByLabelText(/Publieke sleutelset/);
  await user.click(source);
  await user.paste('voor deze componentproef geplakte tekst');
  await user.click(card.getByRole('button', { name: 'Controleren' }));
  expect(await card.findByText(next.fingerprint)).toBeVisible();

  const field = card.getByLabelText(/Vingerafdruk uit de Hub/);
  await user.type(field, '0000{Enter}');
  expect(field).toHaveAttribute('aria-invalid', 'true');
  expect(field).toHaveAccessibleDescription('De vingerafdruk komt niet overeen. Neem de vingerafdruk uit de Hub over.');
  expect(calls.filter((c) => c.url.endsWith(path) && c.method === 'PUT')).toHaveLength(0);

  await user.clear(field);
  await user.type(field, 'oalb 2c3d 4e5f 6g7h{Enter}');
  expect(await card.findByText('Vastgelegde vingerafdruk')).toBeVisible();
  expect(card.queryByRole('textbox')).toBeNull();
  await user.click(card.getByRole('button', { name: 'Opnieuw koppelen' }));
  expect(card.getByLabelText(/Publieke sleutelset/)).toHaveValue('');
  expect(card.queryByLabelText(/Vingerafdruk uit de Hub/)).toBeNull();
  expect(calls.filter((c) => c.url.endsWith(path) && c.method === 'PUT')).toHaveLength(1);
});

it('toont het onderdeel alleen aan de beheerder', async () => {
  const calls = mockApi(() => undefined);
  render(<Providers user={viewer}><SystemPage /></Providers>);
  expect(await screen.findByRole('heading', { name: 'Systeem' })).toBeVisible();
  expect(screen.queryByRole('region', { name: title })).toBeNull();
  expect(calls.some((c) => c.url.includes(path))).toBe(false);
});

it('toont oude en nieuwe vingerafdruk en legt niets vast bij annuleren', async () => {
  const { card, calls, user } = open(old);
  await user.click(await card.findByRole('button', { name: 'Sleutelrotatie controleren' }));
  await user.type(card.getByLabelText(/Ondertekende rotatieverklaringen/), 'verklaringen uit de Hub');
  await user.click(card.getByRole('button', { name: 'Controleren' }));
  expect(await card.findByText('Rotatie wacht op bevestiging.')).toBeVisible();
  expect(card.getByText(old.fingerprint)).toBeVisible();
  expect(card.getByText(next.fingerprint)).toBeVisible();
  await user.click(card.getByRole('button', { name: 'Annuleren' }));
  expect(card.queryByText(next.fingerprint)).toBeNull();
  expect(card.getByText(old.fingerprint)).toBeVisible();
  expect(calls.some((c) => c.method === 'PUT')).toBe(false);
});

it('opent bij schade alleen na de expliciete herstelhandeling een plakveld', async () => {
  const { card, user } = open({ state: 'damaged' });
  const recover = await card.findByRole('button', { name: 'Sleutelset herstellen' });
  expect(card.queryByRole('textbox')).toBeNull();
  await user.click(recover);
  expect(card.getByLabelText(/Publieke sleutelset/)).toHaveFocus();
});

function SystemWithRefreshedRole() {
  const me = useQuery({ queryKey: qk.me, queryFn: api.me, staleTime: Infinity });
  return <UserContext.Provider value={me.data ?? admin}><SystemPage /></UserContext.Provider>;
}

it.each(['GET', 'POST', 'PUT'])('wist invoer en pinstatus en ververst de rol bij HTTP 403 op %s', async (deniedMethod) => {
  const client = newClient();
  const denied = { status: 403, body: { code: 'forbidden', message: 'Alleen een beheerder mag dit doen.' } };
  let role = admin;
  const calls = mockApi((method, url) => {
    if (url.endsWith('/auth/me')) return { body: role };
    if (url.endsWith(path) && method === 'GET') return deniedMethod === 'GET' ? denied : { body: old, headers };
    if (url.endsWith(path + '/verify')) return deniedMethod === 'POST' ? denied : { body: next };
    if (url.endsWith(path) && method === 'PUT') return denied;
  });
  if (deniedMethod === 'GET') role = { ...viewer, id: admin.id };
  const user = userEvent.setup();
  // Houd bij GET de bestaande rol vast totdat de geweigerde kaartstatus de rol ververst.
  client.setQueryData(qk.me, admin);
  render(<Providers client={client}><SystemWithRefreshedRole /></Providers>);
  const region = screen.getByRole('region', { name: title });
  const card = within(region);
  if (deniedMethod !== 'GET') {
    await user.click(await card.findByRole('button', { name: 'Opnieuw koppelen' }));
    await user.type(card.getByLabelText(/Publieke sleutelset/), 'geplakte proeftekst');
    if (deniedMethod === 'POST') role = { ...viewer, id: admin.id };
    await user.click(card.getByRole('button', { name: 'Controleren' }));
    if (deniedMethod === 'PUT') {
      await user.type(await card.findByLabelText(/Vingerafdruk uit de Hub/), next.fingerprint);
      role = { ...viewer, id: admin.id };
      await user.click(card.getByRole('button', { name: 'Vastleggen' }));
    }
  }
  await waitFor(() => expect(calls.some(c => c.method === deniedMethod && c.url.includes(path))).toBe(true));
  await waitFor(() => expect(region).not.toBeInTheDocument());
  expect(client.getQueryData(qk.hubPin)).toBeUndefined();
  await waitFor(() => expect(client.getQueryData(qk.me)).toEqual(role));
  expect(calls.filter(c => c.url.endsWith('/auth/me'))).toHaveLength(1);
});

it('laat een vertraagde bevestiging de gewiste pinstatus na een rolwisseling niet opnieuw vullen', async () => {
  const client = newClient();
  mockApi((method, url) => {
    if (url.endsWith(path) && method === 'GET') return { body: old, headers };
    if (url.endsWith(path + '/verify')) return { body: next };
  });
  let finish!: (result: Awaited<ReturnType<typeof api.confirmHubPin>>) => void;
  const confirm = vi.spyOn(api, 'confirmHubPin').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const user = userEvent.setup();
  const system = (account: typeof admin) => <Providers user={account} client={client}><SystemPage /></Providers>;
  const { rerender } = render(system(admin));
  try {
    const card = within(screen.getByRole('region', { name: title }));
    await user.click(await card.findByRole('button', { name: 'Opnieuw koppelen' }));
    await user.type(card.getByLabelText(/Publieke sleutelset/), 'geplakte proeftekst');
    await user.click(card.getByRole('button', { name: 'Controleren' }));
    await user.type(await card.findByLabelText(/Vingerafdruk uit de Hub/), next.fingerprint);
    await user.click(card.getByRole('button', { name: 'Vastleggen' }));
    expect(confirm).toHaveBeenCalledOnce();
    rerender(system({ ...viewer, id: admin.id }));
    expect(screen.queryByRole('region', { name: title })).toBeNull();
    await waitFor(() => expect(client.getQueryData(qk.hubPin)).toBeUndefined());
    await act(async () => finish({ data: { ...next, state: 'pinned' }, etag: 'later' }));
    expect(client.getQueryData(qk.hubPin)).toBeUndefined();
  } finally {
    confirm.mockRestore();
  }
});
