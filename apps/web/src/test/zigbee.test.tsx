import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ConnectionProvider, HomeProvider, PendingProvider } from '../state';
import { UserContext } from '../user';
import { DevicesPage } from '../pages/Devices';
import { ZoneSettings } from '../pages/Climate';
import type { Device } from '../api/types';
import { device, zone } from './fixtures';

function wrap(children: ReactNode) {
  localStorage.setItem('shc.home', 'h1');
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectionProvider initial={{ apiReachable: true, wsState: 'open' }}>
        <PendingProvider>
          <UserContext.Provider value={{ id: 'u1', username: 'admin', role: 'admin' }}>
            <HomeProvider>{children}</HomeProvider>
          </UserContext.Provider>
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}

const dev = (over: Partial<Device>): Device => device({ id: 'x', ...over });

function mockApi(route: (method: string, url: string, body: unknown) => unknown) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    const method = init.method ?? 'GET';
    calls.push({ method, url, body });
    const r = route(method, url, body) ?? [];
    const status = (r as { __status?: number }).__status ?? 200;
    return new Response(JSON.stringify(r), { status, headers: { 'content-type': 'application/json' } });
  }));
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('Zigbee sensor koppelen', () => {
  it('offers setup when zigbee is not configured', async () => {
    const calls = mockApi((_m, url) => {
      if (url.endsWith('/integrations')) return [{ id: null, provider: 'zigbee', label: 'Zigbee', state: 'not_configured', detail: '', last_ok_at: null }];
      if (url.endsWith('/zigbee/instructions')) return { steps: ['Stap'], needs_manufacturer_app: null };
      if (url.endsWith('/zigbee/setup')) return dev({ id: 'b', kind: 'zigbee_bridge' });
      return [];
    });
    render(wrap(<DevicesPage />));
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Zigbee instellen' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/zigbee/setup'))?.body).toEqual({ home_id: 'h1' }));
  });

  it('opens permit-join with countdown, highlights new sensors and assigns a room', async () => {
    let joined = false;
    const calls = mockApi((m, url) => {
      if (url.endsWith('/integrations')) return [{ id: 'i1', provider: 'zigbee', label: 'Zigbee', state: 'ok', detail: 'Verbonden', last_ok_at: null }];
      if (url.endsWith('/zigbee/instructions')) return { steps: ['Houd de knop ingedrukt'], needs_manufacturer_app: null };
      if (url.endsWith('/homes/h1/rooms')) return [{ id: 'r1', name: 'Woonkamer', function: 'living' }, { id: 'r2', name: 'Slaapkamer', function: 'bedroom' }];
      if (url.endsWith('/zigbee/permit-join')) {
        joined = true;
        return { __status: 202, command_id: 'c1', open_until: new Date(Date.now() + 120_000).toISOString() };
      }
      if (url.endsWith('/homes/h1/devices'))
        return joined ? [dev({ id: 'old', name: 'Oude sensor' }), dev({ id: 'new', name: 'Aqara 2' })] : [dev({ id: 'old', name: 'Oude sensor' })];
      if (m === 'PATCH') return dev({ id: 'new', name: 'Aqara 2', room: 'Slaapkamer' });
      return [];
    });
    const { container } = render(wrap(<DevicesPage />));
    const user = userEvent.setup();
    await screen.findByText('Oude sensor');
    await user.click(await screen.findByRole('button', { name: 'Koppelen starten (2 min)' }));
    expect(calls.find((c) => c.url.endsWith('/zigbee/permit-join'))?.body).toEqual({ home_id: 'h1', seconds: 120 });
    expect(await screen.findByTestId('join-countdown')).toHaveTextContent(/nog [12]:\d\d/);
    expect(screen.getByRole('button', { name: 'Koppelen starten (2 min)' })).toBeDisabled();

    // The 5 s poll while the window is open picks up the new device.
    const list = await screen.findByRole('list', { name: 'Nieuwe sensoren' }, { timeout: 7000 });
    expect(within(list).getByText('Nieuw gevonden')).toBeInTheDocument();
    expect(within(list).getByText('Aqara 2')).toBeInTheDocument();
    expect(within(list).queryByText('Oude sensor')).not.toBeInTheDocument();
    await user.selectOptions(within(list).getByLabelText('Ruimte voor Aqara 2'), 'r2');
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ url: '/api/v1/devices/new', body: { room_id: 'r2' } }));
    expect(container).toBeTruthy();
  }, 15000);
});

describe('Zone-koppeling', () => {
  it('prefills unit and sensor from zone_view', async () => {
    mockApi((_m, url) => {
      if (url.endsWith('/homes/h1/devices')) return [dev({ id: 'u1', kind: 'ac_unit', name: 'Gree woonkamer', provider: 'gree' }), dev({ id: 's1', name: 'Sensor woonkamer' })];
      return [];
    });
    render(wrap(<ZoneSettings zone={zone({ unit_device_id: 'u1', sensor_device_id: 's1' })} homeId="h1" />));
    await screen.findByRole('option', { name: 'Gree woonkamer' });
    expect(screen.getByLabelText('Airco-unit')).toHaveValue('u1');
    expect(screen.getByLabelText('Temperatuursensor')).toHaveValue('s1');
  });
});
