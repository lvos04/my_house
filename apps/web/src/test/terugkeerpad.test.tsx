import { afterEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { accessLoginUrl, ACCESS_RELOAD_PARAM } from '../api/client';
import { admin, FakeWebSocket, mockApi, newClient } from './helpers';

afterEach(() => {
  vi.restoreAllMocks();
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
  const gebruikersnaam = await screen.findByLabelText('Gebruikersnaam');

  // Begin na de initialisatie van BrowserRouter, die de historystaat instelt.
  // jsdom maakt de publieke Location-methoden en de href-setter niet configureerbaar.
  const implementatieSleutel = Object.getOwnPropertySymbols(window.location).find((sleutel) => sleutel.description === 'impl');
  if (!implementatieSleutel) throw new Error('De Location-implementatie van jsdom ontbreekt');
  const locatie = Reflect.get(window.location, implementatieSleutel) as Pick<Location, 'assign' | 'replace' | 'href' | 'reload'>;
  const navigatiepogingen = {
    'location.assign': vi.spyOn(locatie, 'assign'),
    'location.replace': vi.spyOn(locatie, 'replace'),
    'location.href': vi.spyOn(locatie, 'href', 'set'),
    'location.reload': vi.spyOn(locatie, 'reload'),
    'history.pushState': vi.spyOn(window.history, 'pushState'),
    'history.replaceState': vi.spyOn(window.history, 'replaceState'),
    'history.go': vi.spyOn(window.history, 'go'),
    'history.back': vi.spyOn(window.history, 'back'),
    'history.forward': vi.spyOn(window.history, 'forward'),
  };
  const gebruiker = userEvent.setup();
  await gebruiker.type(gebruikersnaam, 'testgebruiker');
  await gebruiker.type(screen.getByLabelText('Wachtwoord'), 'testwaarde');
  await gebruiker.click(screen.getByRole('button', { name: 'Inloggen' }));
  await screen.findByRole('heading', { name: 'Woning aanmaken' });
  expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/auth/login'))).toBe(true);
  for (const [naam, spy] of Object.entries(navigatiepogingen)) {
    expect(spy, naam).not.toHaveBeenCalled();
  }
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
