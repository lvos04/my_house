import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SystemPage } from '../pages/System';
import { mockApi, Providers, viewer } from './helpers';

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
