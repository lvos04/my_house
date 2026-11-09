import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { api, isActionRequired } from '../api/client';
import { ConnectionProvider, HomeProvider, PendingProvider } from '../state';
import { UserContext } from '../user';
import { DevicesPage, PROTECTED_TEXT } from '../pages/Devices';
import { SystemPage } from '../pages/System';
import type { Device } from '../api/types';
import { device } from './fixtures';

type Route = (method: string, url: string, body: unknown) => { status?: number; body?: unknown } | undefined;

function mockApi(route: Route) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    const method = init.method ?? 'GET';
    calls.push({ method, url, body });
    const r = route(method, url, body) ?? { body: [] };
    return new Response(JSON.stringify(r.body ?? {}), { status: r.status ?? 200, headers: { 'content-type': 'application/json' } });
  }));
  return calls;
}

function wrap(children: ReactNode) {
  localStorage.setItem('shc.home', 'h1');
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectionProvider initial={{ apiReachable: true, wsState: 'open' }}>
        <PendingProvider>
          <UserContext.Provider value={{ id: 'u1', username: 'admin', role: 'admin' }}>
            <MemoryRouter>
              <HomeProvider>{children}</HomeProvider>
            </MemoryRouter>
          </UserContext.Provider>
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}

const boiler: Device = device({
  id: 'd1', provider: 'homewizard', kind: 'energy_socket', name: 'Boiler-stekker', stable_identity: 'abc',
  roles: ['boiler'], allowed_roles: ['airco', 'boiler'], capabilities: ['measured_power', 'relay'], protected: true,
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('api bodies (backend contract)', () => {
  it('discover sends home_id; pair sends home_id/host/name; backup sends passphrase + include_secrets', async () => {
    const calls = mockApi(() => ({ body: {} }));
    await api.discover('hue', 'h1');
    await api.pair('hue', { home_id: 'h1', host: '192.168.1.2', name: 'Bridge' });
    await api.createBackup({ passphrase: 'lange-wachtwoordzin', include_secrets: false });
    await api.bindDevice('d1', 'airco');
    await api.putZonePolicy('z1', { band_c: 0.5, min_interval_s: 900, max_step_c: 1, max_starts_per_hour: 3, stale_after_s: 1800 });
    expect(calls.map((c) => [c.method, c.url, c.body])).toEqual([
      ['POST', '/api/v1/integrations/hue/discover', { home_id: 'h1' }],
      ['POST', '/api/v1/integrations/hue/pair', { home_id: 'h1', host: '192.168.1.2', name: 'Bridge' }],
      ['POST', '/api/v1/system/backups', { passphrase: 'lange-wachtwoordzin', include_secrets: false }],
      ['POST', '/api/v1/devices/d1/bind', { role: 'airco' }],
      ['PUT', '/api/v1/zones/z1/policy', { band_c: 0.5, min_interval_s: 900, max_step_c: 1, max_starts_per_hour: 3, stale_after_s: 1800 }],
    ]);
    expect(api.backupDownloadUrl('b1')).toBe('/api/v1/system/backups/b1/download');
    expect(isActionRequired({ status: 'action_required', steps: [], retry_after_s: 30 })).toBe(true);
    expect(isActionRequired({ id: 'd1' })).toBe(false);
  });
});

describe('Apparaten', () => {
  it('shows protected lock text, untested badge, and retries pairing after action_required', async () => {
    let pairs = 0;
    const calls = mockApi((_m, url) => {
      if (url.endsWith('/homes/h1/devices')) return { body: [boiler] };
      if (url.endsWith('/integrations/zigbee/instructions')) return { body: { provider: 'zigbee', label: 'Zigbee', steps: ['Houd de knop 5 seconden ingedrukt'], needs_manufacturer_app: null, works_locally: true } };
      if (url.endsWith('/integrations/hue/pair')) {
        pairs++;
        return pairs === 1
          ? { status: 202, body: { status: 'action_required', steps: ['Druk op de link-knop van de Hue Bridge'], retry_after_s: 30 } }
          : { body: { ...boiler, id: 'd2', name: 'Hue Bridge', protected: false, roles: [] } };
      }
      return { body: [] };
    });
    render(wrap(<DevicesPage />));
    expect(await screen.findByText(PROTECTED_TEXT)).toBeInTheDocument();
    expect(screen.getByText('nog niet getest')).toBeInTheDocument();
    expect(await screen.findByText('Houd de knop 5 seconden ingedrukt')).toBeInTheDocument();

    const user = userEvent.setup();
    const hostInput = screen.getByLabelText('IP-adres Hue Bridge');
    await user.type(hostInput, '192.168.1.2');
    const hueCard = hostInput.closest('article')!;
    await user.click(Array.from(hueCard.querySelectorAll('button')).find((b) => b.textContent === 'Koppelen' && b.getAttribute('type') === 'submit')!);
    expect(await screen.findByText('Druk op de link-knop van de Hue Bridge')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Opnieuw proberen' }));
    expect(await screen.findByText('Gekoppeld: Hue Bridge')).toBeInTheDocument();
    const pairCalls = calls.filter((c) => c.url.endsWith('/hue/pair'));
    expect(pairCalls).toHaveLength(2);
    expect(pairCalls[1].body).toEqual({ home_id: 'h1', host: '192.168.1.2' });

    // role binding: choosing a role alone does nothing; it binds only after "Koppelen" and confirmation
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await user.selectOptions(screen.getByLabelText('Rol van Boiler-stekker'), 'airco');
    expect(calls.some((c) => c.url.endsWith('/devices/d1/bind'))).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Rol koppelen: Boiler-stekker' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/devices/d1/bind'))).toBe(true));
    expect(confirmSpy).toHaveBeenCalled();
  });
});

describe('Systeem', () => {
  it('shows safe-mode card and releases it', async () => {
    const calls = mockApi((_m, url) => {
      if (url.endsWith('/system/status')) return { body: { checks: [{ key: 'safe_mode', label: 'Veilige modus', state: 'warn', detail: 'Actief na herstel' }] } };
      if (url.endsWith('/system/settings')) return { body: { lan_url: 'http://192.168.1.20:8080', remote_url: null, safe_mode: true } };
      if (url.endsWith('/safe-mode/release')) return { body: { safe_mode: false } };
      if (url.endsWith('/homes')) return { body: [{ id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true }] };
      return { body: [] };
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(wrap(<SystemPage />));
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Apparaten kloppen — veilige modus opheffen' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/system/safe-mode/release'))).toBe(true));
    expect(await screen.findByDisplayValue('http://192.168.1.20:8080')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Archiveren (verhuisd)' })).toBeInTheDocument();
    expect(screen.getByLabelText('Geheimen meenemen')).toBeChecked();
  });
});
