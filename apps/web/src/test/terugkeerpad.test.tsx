import { afterEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { accessLoginUrl, ACCESS_RELOAD_PARAM } from '../api/client';
import { admin, FakeWebSocket, mockApi, newClient } from './helpers';

afterEach(() => {
  vi.unstubAllGlobals();
  FakeWebSocket.reset();
  window.history.replaceState({}, '', '/');
});

it('negeert een extern terugkeerpad na inloggen', async () => {
  window.history.replaceState({}, '', '/?next=https://evil.example');
  const eigenAdres = window.location.href;
  vi.stubGlobal('WebSocket', FakeWebSocket);
  let ingelogd = false;
  const calls = mockApi((method, url) => {
    if (url.endsWith('/auth/status')) return { body: { setup_required: false, user: ingelogd ? admin : null } };
    if (url.endsWith('/auth/login') && method === 'POST') {
      ingelogd = true;
      return { body: { user: admin } };
    }
    if (url.endsWith('/me')) return { body: admin };
    if (url.endsWith('/homes')) return { body: [] };
    return undefined;
  });
  render(<App client={newClient()} />);
  const gebruiker = userEvent.setup();
  await gebruiker.type(await screen.findByLabelText('Gebruikersnaam'), 'testgebruiker');
  await gebruiker.type(screen.getByLabelText('Wachtwoord'), 'testwaarde');
  await gebruiker.click(screen.getByRole('button', { name: 'Inloggen' }));
  await screen.findByRole('heading', { name: 'Woning aanmaken' });
  expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/auth/login'))).toBe(true);
  expect(window.location.href).toBe(eigenAdres);
});

it('geeft Access uitsluitend het huidige eigen adres als terugkeeradres', () => {
  const huidig = `https://huis.example.nl/energie?next=https://evil.example&${ACCESS_RELOAD_PARAM}=5`;
  const link = new URL(accessLoginUrl({ href: huidig }), huidig);
  const terug = new URL(link.searchParams.get('redirect_url')!);
  expect(link.origin).toBe(new URL(huidig).origin);
  expect(terug.origin).toBe(new URL(huidig).origin);
  expect(terug.pathname).toBe('/energie');
  expect(terug.searchParams.get('next')).toBe('https://evil.example');
  expect(terug.searchParams.has(ACCESS_RELOAD_PARAM)).toBe(false);
});
