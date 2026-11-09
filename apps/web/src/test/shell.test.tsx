import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppRoutes } from '../App';
import { browserTimeZone, APP_VERSION } from '../format';
import type { Home } from '../api/types';
import { admin, FakeWebSocket, mockApi, Providers, viewer } from './helpers';
import { overview } from './fixtures';

beforeEach(() => vi.stubGlobal('WebSocket', FakeWebSocket));
afterEach(() => {
  vi.unstubAllGlobals();
  FakeWebSocket.reset();
  localStorage.clear();
});

const home: Home = { id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true };

describe('no home yet (item 1)', () => {
  it('shows an inline "Woning aanmaken" form with the browser time zone, then next steps', async () => {
    let homes: Home[] = [];
    const calls = mockApi((m, url, body) => {
      if (url.endsWith('/homes') && m === 'POST') {
        homes = [{ ...home, name: (body as { name: string }).name, timezone: (body as { timezone: string }).timezone }];
        return { body: homes[0] };
      }
      if (url.endsWith('/homes')) return { body: homes };
      if (url.endsWith('/overview')) return { body: overview };
      return undefined;
    });
    render(<Providers homeId={null}><AppRoutes user={admin} /></Providers>);
    expect(await screen.findByRole('heading', { name: 'Woning aanmaken' })).toBeInTheDocument();
    const tz = screen.getByLabelText('Tijdzone') as HTMLSelectElement;
    expect(tz.value).toBe(browserTimeZone());
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Naam van de woning'), 'Ons huis');
    await user.click(screen.getByRole('button', { name: 'Woning aanmaken' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/homes'))?.body).toEqual({ name: 'Ons huis', timezone: browserTimeZone() }));
    const next = await screen.findByRole('region', { name: /Volgende stappen/ });
    expect(within(next).getByRole('link', { name: 'Stel het thuisadres in' })).toHaveAttribute('href', '/systeem#adressen');
    expect(within(next).getByRole('link', { name: 'Koppel je apparaten' })).toHaveAttribute('href', '/apparaten');
    expect(screen.queryByRole('heading', { name: 'Woning aanmaken' })).not.toBeInTheDocument();
  });

  it('Systeem stays reachable without a home (never a dead end)', async () => {
    mockApi((_m, url) => (url.endsWith('/homes') ? { body: [] } : undefined));
    render(<Providers homeId={null} route="/systeem"><AppRoutes user={admin} /></Providers>);
    expect(await screen.findByRole('heading', { level: 1, name: 'Systeem' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Woning aanmaken' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Adressen' })).toBeInTheDocument();
  });

  it('a viewer is told to ask an admin', async () => {
    mockApi((_m, url) => (url.endsWith('/homes') ? { body: [] } : undefined));
    render(<Providers homeId={null} user={viewer}><AppRoutes user={viewer} /></Providers>);
    expect(await screen.findByText(/Vraag een beheerder om er een aan te maken/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Naam van de woning')).not.toBeInTheDocument();
  });
});

describe('shell (item 40)', () => {
  it('mobile bottom bar has 4 main pages and a "Meer" sheet with logout and the version', async () => {
    const calls = mockApi((_m, url) => {
      if (url.endsWith('/homes')) return { body: [home] };
      if (url.endsWith('/overview')) return { body: overview };
      if (url.endsWith('/auth/logout')) return { status: 204 };
      return undefined;
    });
    render(<Providers><AppRoutes user={admin} /></Providers>);
    const bar = await screen.findByRole('navigation', { name: 'Hoofdmenu (mobiel)' });
    expect(within(bar).getAllByRole('link').map((a) => a.textContent)).toEqual(['Overzicht', 'Klimaat', 'Licht', 'Energie']);
    const user = userEvent.setup();
    await user.click(within(bar).getByRole('button', { name: 'Meer' }));
    const sheet = screen.getByRole('dialog', { name: 'Meer' });
    expect(within(sheet).getByRole('link', { name: 'Systeem' })).toHaveAttribute('href', '/systeem');
    expect(within(sheet).getByRole('link', { name: 'Account' })).toHaveAttribute('href', '/account');
    expect(within(sheet).getByText(`Versie ${APP_VERSION}`)).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(within(bar).getByRole('button', { name: 'Meer' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Uitloggen' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/auth/logout'))).toBe(true));
  });

  it('opens the live connection for the selected home', async () => {
    mockApi((_m, url) => (url.endsWith('/homes') ? { body: [home] } : url.endsWith('/overview') ? { body: overview } : undefined));
    render(<Providers><AppRoutes user={admin} /></Providers>);
    await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
    expect(FakeWebSocket.last().url).toMatch(/\/api\/v1\/ws\?home_id=h1$/);
  });
});
