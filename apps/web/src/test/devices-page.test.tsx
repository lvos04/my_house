import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DevicesPage, PROTECTED_TEXT } from '../pages/Devices';
import type { Integration } from '../api/types';
import { bindPlan, offeredRoles } from '../devices/RoleControl';
import { calibrationBody } from '../devices/Calibration';
import { replacementCandidates } from '../devices/DeviceTools';
import { healthLines } from '../devices/Integrations';
import { mockApi, Providers, viewer } from './helpers';
import { device } from './fixtures';

const p1 = device({ id: 'p1', provider: 'homewizard', kind: 'p1_meter', name: 'P1-meter', roles: ['grid'], allowed_roles: ['grid'] });
const socketA = device({ id: 'sa', provider: 'homewizard', kind: 'energy_socket', name: 'Stekker A', roles: ['airco'], allowed_roles: ['airco', 'boiler'] });
const socketB = device({ id: 'sb', provider: 'homewizard', kind: 'energy_socket', name: 'Stekker B', roles: [], allowed_roles: ['airco', 'boiler'] });
const boiler = device({
  id: 'bo', provider: 'homewizard', kind: 'energy_socket', name: 'Boilerstekker', roles: ['boiler'], allowed_roles: ['airco', 'boiler'], protected: true,
  details: { relay: { on: false, switch_lock: false, state: 'ok', led_brightness: null } },
});
const sensor = device({
  id: 's1', name: 'Sensor woonkamer', allowed_roles: ['outdoor_temp'], model: 'WSDCGQ11LM', calibration: { temperature_offset: 0.5, humidity_offset: 0 },
  details: { zigbee: { friendly_name: '0x00158d0001a2b3c4', interview_completed: true, power_source: 'Battery', availability: 'online' } },
});
const sensor2 = device({ id: 's2', name: 'Sensor slaapkamer', allowed_roles: ['outdoor_temp'] });
const all = [p1, socketA, socketB, boiler, sensor, sensor2];

const hue: Integration = {
  id: 'i1', provider: 'hue', label: 'Hue Bridge', provider_label: 'Philips Hue', home_id: 'h1', enabled: true, identity: 'ecb5fa', host: '192.168.1.20',
  state: 'warn', detail: 'Niet bereikbaar sinds 2 min', last_ok_at: new Date(Date.now() - 300_000).toISOString(), code: 'device_offline',
  connected_since: new Date(Date.now() - 3 * 3600_000).toISOString(), missed_polls: 2, checked_at: new Date().toISOString(), has_diagnostics: false,
};

function route(extra: (m: string, url: string, body: unknown) => ReturnType<Parameters<typeof mockApi>[0]> = () => undefined) {
  return (m: string, url: string, body: unknown) => {
    const r = extra(m, url, body);
    if (r) return r;
    if (url.endsWith('/homes/h1/devices')) return { body: all };
    if (url.endsWith('/integrations')) return { body: [hue, { id: null, provider: 'gree', label: 'Gree airco', provider_label: 'Gree airco', state: 'not_configured', detail: '', last_ok_at: null }] };
    if (url.endsWith('/homes/h1/rooms')) return { body: [{ id: 'r1', name: 'Woonkamer', function: 'living' }] };
    if (url.endsWith('/bind') || url.endsWith('/unbind')) return { body: socketB };
    return { body: [] };
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('roles (item 24, kleine punten)', () => {
  it('offers only roles that fit the device and asks before re-binding', () => {
    expect(offeredRoles(p1)).toEqual([]);
    expect(offeredRoles(socketB)).toEqual(['airco', 'boiler']);
    expect(offeredRoles(sensor)).toEqual(['outdoor_temp']);
    const plan = bindPlan(socketB, 'airco', all);
    expect(plan.confirm).toBe(true);
    expect(plan.message).toMatch(/Stekker A heeft deze rol nu\. Die wordt losgekoppeld/);
    expect(bindPlan(sensor, 'outdoor_temp', all)).toMatchObject({ confirm: false });
    expect(bindPlan(boiler, 'airco', all).message).toMatch(/beschermd/);
  });

  it('filters the role picker, confirms a re-bind, binds a free role directly and unbinds with confirmation', async () => {
    const calls = mockApi(route());
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<Providers><DevicesPage /></Providers>);
    const user = userEvent.setup();
    const cardB = await screen.findByTestId('device-sb');
    const select = within(cardB).getByLabelText('Rol van Stekker B');
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['Kies een rol…', 'Airco totaal (kWh-meter) (nu: Stekker A)', 'Boiler (alleen meten) (nu: Boilerstekker)']);
    // The P1 meter already has its only possible role: no picker.
    expect(within(screen.getByTestId('device-p1')).queryByRole('combobox')).not.toBeInTheDocument();

    await user.selectOptions(select, 'airco');
    expect(calls.some((c) => c.url.endsWith('/bind'))).toBe(false); // choosing alone does nothing
    await user.click(within(cardB).getByRole('button', { name: 'Rol koppelen: Stekker B' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/Stekker A heeft deze rol nu/));
    expect(calls.some((c) => c.url.endsWith('/bind'))).toBe(false); // cancelled
    confirm.mockReturnValue(true);
    await user.click(within(cardB).getByRole('button', { name: 'Rol koppelen: Stekker B' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/api/v1/devices/sb/bind')?.body).toEqual({ role: 'airco' }));

    // A free role on a device without roles: bound at once, no question.
    confirm.mockClear();
    const cardS2 = screen.getByTestId('device-s2');
    await user.selectOptions(within(cardS2).getByLabelText('Rol van Sensor slaapkamer'), 'outdoor_temp');
    await user.click(within(cardS2).getByRole('button', { name: 'Rol koppelen: Sensor slaapkamer' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/api/v1/devices/s2/bind')?.body).toEqual({ role: 'outdoor_temp' }));
    expect(confirm).not.toHaveBeenCalled();

    await user.click(within(screen.getByTestId('device-sa')).getByRole('button', { name: 'Rol Airco totaal (kWh-meter) loskoppelen van Stekker A' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/geschiedenis blijft bewaard/));
    await waitFor(() => expect(calls.find((c) => c.url === '/api/v1/devices/sa/unbind')?.body).toEqual({ role: 'airco' }));
  });
});

describe('protected socket (item 15, 43)', () => {
  it('shows relay and switch lock with Dutch instructions, and offers no switching', async () => {
    mockApi(route());
    render(<Providers><DevicesPage /></Providers>);
    const card = await screen.findByTestId('device-bo');
    expect(within(card).getByText(PROTECTED_TEXT)).toBeInTheDocument();
    const relay = within(card).getByTestId('relay');
    expect(relay).toHaveTextContent('Stekker staat UIT');
    expect(relay).toHaveTextContent('De boiler krijgt geen stroom');
    expect(relay).toHaveTextContent('Switch lock staat uit');
    expect(relay).toHaveTextContent('HomeWizard Energy-app');
    const buttons = within(card).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent ?? '');
    expect(buttons.some((n) => /aanzetten|uitzetten|schakel/i.test(n))).toBe(false);
  });
});

describe('Zigbee sensor and calibration (item 8)', () => {
  it('shows the friendly name and power source, and saves only changed offsets', async () => {
    const calls = mockApi(route((_m, url) => (url.endsWith('/devices/s1/calibration') ? { body: { ...sensor, calibration: { temperature_offset: -0.3, humidity_offset: 2 } } } : undefined)));
    render(<Providers><DevicesPage /></Providers>);
    const user = userEvent.setup();
    const card = await screen.findByTestId('device-s1');
    expect(within(card).getByText('Naam in Zigbee2MQTT: 0x00158d0001a2b3c4')).toBeInTheDocument();
    expect(within(card).getByText('Voeding: batterij')).toBeInTheDocument();
    expect(within(card).getByText('Correctie: temperatuur +0,5 °C')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Zigbee-sensoren' })).getByText('Sensor woonkamer')).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Beheren: Sensor woonkamer' }));
    const form = within(card).getByRole('form', { name: 'Kalibratie Sensor woonkamer' });
    const t = within(form).getByLabelText('Temperatuurcorrectie (°C)');
    expect(t).toHaveValue('0,5');
    await user.clear(t);
    await user.type(t, '12');
    await user.click(within(form).getByRole('button', { name: 'Correctie opslaan' }));
    expect(within(form).getByRole('alert')).toHaveTextContent('tussen −10 en 10');
    expect(calls.some((c) => c.url.endsWith('/calibration'))).toBe(false);

    await user.clear(t);
    await user.type(t, '-0,3');
    await user.type(within(form).getByLabelText('Vochtigheidscorrectie (%)'), '2');
    await user.click(within(form).getByRole('button', { name: 'Correctie opslaan' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/devices/s1/calibration'))).toMatchObject({ method: 'PATCH', body: { temperature_offset: -0.3, humidity_offset: 2 } }));
  });

  it('builds the calibration body', () => {
    expect(calibrationBody(sensor, { temperature: '0,5', humidity: '' }, true)).toEqual({ body: {}, errors: [] });
    expect(calibrationBody(sensor, { temperature: '', humidity: '−4' }, true)).toEqual({ body: { temperature_offset: 0, humidity_offset: -4 }, errors: [] });
    expect(calibrationBody(sensor, { temperature: 'x', humidity: '40' }, true).errors).toHaveLength(2);
    expect(calibrationBody(sensor, { temperature: '1', humidity: '40' }, false)).toEqual({ body: { temperature_offset: 1 }, errors: [] });
  });
});

describe('device tools (item 29, 43)', () => {
  it('tests read-only, confirms "getest", shows diagnostics to copy, replaces and archives with confirmation', async () => {
    const calls = mockApi(route((m, url) => {
      if (url.endsWith('/devices/s1/test'))
        return { body: { device_id: 's1', ok: false, checked_at: new Date().toISOString(), checks: [{ key: 'seen', label: 'Laatste bericht', state: 'ok', detail: 'Laatste bericht zojuist' }, { key: 'connection', label: 'Verbinding', state: 'error', detail: 'Sleutel past niet, koppel opnieuw.', code: 'key_mismatch' }], readings: { temperature: { value: 20.4, unit: '°C', quality: 'measured', at: new Date().toISOString(), raw: null } }, note: 'Er is niets geschakeld; alleen uitgelezen.' } };
      if (url.endsWith('/devices/s1/tested')) return { body: { ...sensor, compatibility: 'tested', tested_at: '2026-11-09T10:00:00Z' } };
      if (url.endsWith('/devices/s1/diagnostics')) return { body: { diagnostics: { device: { id: 's1' } }, text: '{\n  "device": {\n    "id": "s1"\n  }\n}' } };
      if (url.endsWith('/devices/s1/replace')) return { body: { old: sensor, new: sensor2, moved: { roles: [], zones: 1, mappings: 0 } } };
      if (m === 'DELETE' && url.endsWith('/devices/s1')) return { body: { ...sensor, lifecycle: 'archived' } };
      return undefined;
    }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Providers><DevicesPage /></Providers>);
    const user = userEvent.setup();
    const card = await screen.findByTestId('device-s1');
    await user.click(within(card).getByRole('button', { name: 'Beheren: Sensor woonkamer' }));

    await user.click(within(card).getByRole('button', { name: 'Testen' }));
    const res = await within(card).findByTestId('device-test');
    expect(res).toHaveTextContent('Verbinding: Sleutel past niet, koppel opnieuw.');
    expect(res).toHaveTextContent('Koppel het apparaat opnieuw; de geschiedenis blijft.');
    expect(res).toHaveTextContent('Temperatuur');
    expect(res).toHaveTextContent('Er is niets geschakeld');

    await user.type(within(card).getByLabelText('Opmerking bij de test (optioneel)'), 'meet goed');
    await user.click(within(card).getByRole('button', { name: 'Getest bevestigen' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/devices/s1/tested'))?.body).toEqual({ note: 'meet goed' }));

    await user.click(within(card).getByRole('button', { name: 'Diagnose tonen' }));
    expect(await within(card).findByTestId('diagnostics')).toHaveTextContent('"id": "s1"');
    expect(within(card).getByRole('button', { name: 'Diagnose kopiëren' })).toBeInTheDocument();

    // Replace: only devices that can take over are offered.
    const rep = within(card).getByLabelText('Vervangen door');
    expect(within(rep).getAllByRole('option').map((o) => o.textContent)).toEqual(['Kies het nieuwe apparaat…', 'Sensor slaapkamer']);
    await user.selectOptions(rep, 's2');
    await user.click(within(card).getByRole('button', { name: 'Vervangen' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringMatching(/Sensor woonkamer vervangen door Sensor slaapkamer\?/));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/devices/s1/replace'))?.body).toEqual({ new_device_id: 's2' }));
    expect(await screen.findByText(/Sensor woonkamer is vervangen door Sensor slaapkamer\. Overgezet: 1 zone\./)).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Archiveren' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringMatching(/metingen blijven bewaard/));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/v1/devices/s1')).toBe(true));
    expect(await screen.findByText(/Sensor woonkamer is gearchiveerd/)).toBeInTheDocument();
  });

  it('offers only fitting replacements', () => {
    expect(replacementCandidates(socketA, all).map((d) => d.id)).toEqual(['sb', 'bo']);
    expect(replacementCandidates(p1, all)).toEqual([]);
  });
});

describe('integrations (item 24, 26, 28)', () => {
  it('shows health and changes the address, saving without check only after a failed probe and a confirmation', async () => {
    let patches = 0;
    const calls = mockApi(route((m, url) => {
      if (url.endsWith('/integrations/i1') && m === 'PATCH') {
        patches++;
        return patches === 1
          ? { status: 502, body: { detail: { code: 'device_offline', message: 'Hue Bridge op 192.168.1.30 reageert niet. Het adres is niet gewijzigd. Staat het apparaat uit? Sla dan op zonder controle.' } } }
          : { body: { ...hue, host: '192.168.1.30', health: { health_state: null, health_detail: null, checked_at: null, extra: {} }, config: {}, diagnostics: {}, devices: [] } };
      }
      if (url.endsWith('/integrations/i1'))
        return { body: { ...hue, health: { health_state: 'warn', health_detail: 'Bridge reageert niet', checked_at: null, extra: { eventstream_connected: false, bridge_id: 'ecb5fa' } }, config: { host: '192.168.1.20' }, diagnostics: {}, devices: [{ id: 'l1', name: 'Spot', kind: 'light', online: true, lifecycle: 'active' }] } };
      return undefined;
    }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Providers><DevicesPage /></Providers>);
    const user = userEvent.setup();
    const card = await screen.findByTestId('integration-i1');
    expect(card).toHaveTextContent('Niet bereikbaar sinds 2 min');
    expect(card).toHaveTextContent(/Verbonden sinds .* \(3 uur geleden\)/);
    expect(card).toHaveTextContent('2 uitleesrondes gemist');
    expect(card).toHaveTextContent('vaste IP-reservering');
    expect(screen.getByText('Niet ingesteld: Gree airco.')).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Details: Hue Bridge' }));
    const det = await within(card).findByTestId('integration-detail-i1');
    expect(det).toHaveTextContent('Gebeurtenissen verbonden');
    expect(det).toHaveTextContent('Bridge-ID');
    expect(det).toHaveTextContent('Spot · Lamp · online');

    await user.click(within(card).getByRole('button', { name: 'Adres wijzigen: Hue Bridge' }));
    const host = within(card).getByLabelText('Nieuw IP-adres');
    await user.clear(host);
    await user.type(host, '192.168.1.30');
    await user.click(within(card).getByRole('button', { name: 'Controleren en opslaan' }));
    expect(await within(card).findByText(/op 192.168.1.30 reageert niet/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'PATCH')[0].body).toEqual({ host: '192.168.1.30', verify: true });
    await user.click(within(card).getByRole('button', { name: 'Toch opslaan zonder controle' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/zonder controle/));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')[1]?.body).toEqual({ host: '192.168.1.30', verify: false }));
  });

  it('formats health lines', () => {
    expect(healthLines({ ...hue, connected_since: null, last_ok_at: null, checked_at: null, missed_polls: 1 })).toEqual(['1 uitleesronde gemist']);
  });
});

describe('compatibility and viewers', () => {
  it('loads the compatibility table when opened', async () => {
    const calls = mockApi(route((_m, url) =>
      url.endsWith('/integrations/compatibility')
        ? { body: { providers: [{ provider: 'zigbee', label: 'Zigbee', works_locally: 'ja', needs_manufacturer_app: null }], rows: [{ provider: 'zigbee', model: 'Aqara WSDCGQ11LM', protocol: 'Zigbee2MQTT', local: 'Temperatuur, vochtigheid', vendor_app: null, status: 'tested', devices: [{ id: 's9', name: 'Badkamer', model: 'WSDCGQ11LM', compatibility: 'tested', tested_at: null }], counts: { tested: 1, untested: 0, unsupported: 0 } }], note: '‘Getest’ betekent: in dit huis gecontroleerd.' } }
        : undefined,
    ));
    render(<Providers><DevicesPage /></Providers>);
    await screen.findByTestId('device-s1');
    expect(calls.some((c) => c.url.endsWith('/compatibility'))).toBe(false);
    const details = screen.getByText('Welke apparaten werken?').closest('details')!;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    expect(await screen.findByText('Aqara WSDCGQ11LM')).toBeInTheDocument();
    expect(screen.getByText('Badkamer · getest')).toBeInTheDocument();
  });

  it('shows viewers devices and states but no management', async () => {
    mockApi(route());
    render(<Providers user={viewer}><DevicesPage /></Providers>);
    const card = await screen.findByTestId('device-sa');
    expect(within(card).getByText('Airco totaal (kWh-meter)')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Beheren/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /loskoppelen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Apparaat toevoegen' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Adres wijzigen/ })).not.toBeInTheDocument();
  });
});
