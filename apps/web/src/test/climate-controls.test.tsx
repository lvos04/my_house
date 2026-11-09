import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ZoneCard, ZoneControlPanel, controlText } from '../components/zone';
import { applyCommandUpdate, resetCommands } from '../commands';
import type { Zone } from '../api/types';
import { admin, mockApi, Providers, viewer } from './helpers';
import { zone } from './fixtures';

const off = (over: Partial<Zone> = {}) =>
  zone({ mode: 'off', control: 'off', setpoint_adjustable: false, can_resume: true, notice: "Uitgezet; kies 'Terug naar schema' om het schema weer te volgen.", notice_level: 'info', ...over });

beforeEach(() => resetCommands());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  resetCommands();
});

describe('zone controls when the unit is off (items 11 and 13)', () => {
  it('disables +/−, fan and "Handmatig 2 uur" with "Kies eerst een modus", keeps modes and shows "Terug naar schema"', async () => {
    const calls = mockApi((_m, url) => (url.endsWith('/zones/z1/resume') ? { body: zone({ control: 'schedule', can_resume: false }) } : { body: [] }));
    render(<Providers><ZoneControlPanel zone={off()} /></Providers>);
    expect(screen.getByRole('button', { name: 'Doeltemperatuur hoger' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Doeltemperatuur lager' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Handmatig 2 uur' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Automatisch' })).toBeDisabled();
    expect(screen.getByText(/^Kies eerst een modus\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Doeltemperatuur hoger' })).toHaveAccessibleDescription(/Kies eerst een modus/);
    expect(screen.getByRole('button', { name: 'Verwarmen' })).toBeEnabled();
    expect(screen.getByText('Uitgezet, schema gepauzeerd')).toBeInTheDocument();
    // can_resume also when "uit"
    await userEvent.click(screen.getByRole('button', { name: 'Terug naar schema' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/v1/zones/z1/resume')).toBe(true));
  });

  it('hides "Terug naar schema" without can_resume and says who changed it', () => {
    mockApi(() => ({ body: [] }));
    render(<Providers><ZoneControlPanel zone={zone()} /></Providers>);
    expect(screen.queryByRole('button', { name: 'Terug naar schema' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Doeltemperatuur hoger' })).toBeEnabled();
    expect(controlText(zone({ control: 'manual', override: { until: null, source: 'remote_control' } }))).toBe('Handmatig met de afstandsbediening');
  });

  it('a mode choice is sent and tracked; 409 mode_required shows the Dutch reason', async () => {
    const calls = mockApi((_m, url, body) => {
      if (url.endsWith('/zones/z1/command') && (body as { mode?: string }).mode) return { status: 202, body: { command_id: 'c7', status: 'queued' } };
      if (url.endsWith('/zones/z1/command')) return { status: 409, body: { detail: { code: 'mode_required', message: 'Kies eerst een modus: de airco staat uit.' } } };
      return { body: [] };
    });
    render(<Providers><ZoneControlPanel zone={off()} /></Providers>);
    await userEvent.click(screen.getByRole('button', { name: 'Verwarmen' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/command'))?.body).toEqual({ mode: 'heat' }));
    expect(await screen.findByText('Modus verwarmen:')).toBeInTheDocument();
    expect(screen.getByText(/Wordt verzonden…/)).toBeInTheDocument();
  });

  it('a temperature change the server refuses with 409 mode_required shows "Kies eerst een modus" and reverts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi((_m, url) =>
      url.endsWith('/zones/z1/command') ? { status: 409, body: { detail: { code: 'mode_required', message: 'Kies eerst een modus: de airco staat uit.' } } } : { body: [] },
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    // The unit was just switched off with the remote; the page still has the old state.
    render(<Providers><ZoneControlPanel zone={zone()} /></Providers>);
    await user.click(screen.getByRole('button', { name: 'Doeltemperatuur hoger' }));
    await act(() => vi.advanceTimersByTimeAsync(900));
    const note = await screen.findByText('Kies eerst een modus: de airco staat uit.');
    expect(note).toHaveClass('note', 'warn');
    expect(screen.getByText('21,0°')).toBeInTheDocument();
  });
});

describe('command confirmation (item 23)', () => {
  it('sends one final value after quick +/− presses and shows it as "verzonden" until command.updated confirms', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi((_m, url) => (url.endsWith('/zones/z1/command') ? { status: 202, body: { command_id: 'c1', status: 'queued' } } : { body: [] }));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { rerender } = render(<Providers><ZoneControlPanel zone={zone()} /></Providers>);
    await user.click(screen.getByRole('button', { name: 'Doeltemperatuur hoger' }));
    await user.click(screen.getByRole('button', { name: 'Doeltemperatuur hoger' }));
    expect(screen.getByText('22,0°')).toBeInTheDocument();
    expect(screen.getByText(/wordt zo verzonden/)).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(900));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/command'))).toHaveLength(1));
    expect(calls.find((c) => c.url.endsWith('/command'))!.body).toEqual({ target_c: 22 });
    expect(await screen.findByText(/Wordt verzonden…/)).toBeInTheDocument();

    // The server's immediate zone.updated with the old value is no confirmation.
    rerender(<Providers><ZoneControlPanel zone={zone({ target_c: 21, pending_command: { id: 'c1', status: 'queued', since: null } })} /></Providers>);
    expect(screen.getByText('22,0°')).toBeInTheDocument();
    act(() => applyCommandUpdate({ id: 'c1', status: 'sent' }));
    expect(screen.getByText(/Verzonden, nog niet bevestigd/)).toBeInTheDocument();
    expect(screen.getByText('22,0°')).toBeInTheDocument();
    act(() => applyCommandUpdate({ id: 'c1', status: 'confirmed' }));
    rerender(<Providers><ZoneControlPanel zone={zone({ target_c: 22 })} /></Providers>);
    expect(screen.getByText(/Bevestigd/)).toBeInTheDocument();
    expect(screen.getByText('Doeltemperatuur 22,0°:')).toBeInTheDocument();
  });

  it('shows the Dutch reason when a command fails and reverts the target', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi((_m, url) => (url.endsWith('/zones/z1/command') ? { status: 202, body: { command_id: 'c2', status: 'queued' } } : { body: [] }));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Providers><ZoneCard zone={zone()} /></Providers>);
    await user.click(screen.getByRole('button', { name: 'Doeltemperatuur Woonkamer lager' }));
    expect(screen.getByText('20,5°')).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(900));
    await screen.findByText(/Wordt verzonden…/);
    act(() => applyCommandUpdate({ id: 'c2', status: 'queued', retrying: true, attempts: 2, detail: 'geen antwoord' }));
    expect(screen.getByText(/Mislukt, wordt opnieuw geprobeerd…: geen antwoord/)).toBeInTheDocument();
    act(() => applyCommandUpdate({ id: 'c2', status: 'failed', detail: 'De airco reageert niet.' }));
    expect(screen.getByText(/Mislukt: De airco reageert niet\./)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('20,5°')).not.toBeInTheDocument());
    expect(screen.getByText('21,0°')).toBeInTheDocument();
  });
});

describe('zone without airco and unreachable airco (item 31)', () => {
  const unlinked = zone({
    unit_linked: false, unit_status: 'not_linked', online: false, unit_device_id: null, sensor_device_id: 's1',
    target_c: null, mode: 'off', setpoint_adjustable: false,
    capabilities: { modes: [], fan: false, swing: false, min_c: 16, max_c: 30, step_c: 1 },
    blocked_modes: [], notice: 'Nog geen airco gekoppeld; alleen de sensorwaarden worden getoond. Koppel een airco via Apparaten.', notice_level: 'info',
  });

  it('shows a neutral card with the sensor values and a link to set it up', () => {
    mockApi(() => ({ body: [] }));
    const { container } = render(<Providers user={admin}><ZoneCard zone={unlinked} /></Providers>);
    expect(screen.getByText('Nog geen airco gekoppeld')).toBeInTheDocument();
    expect(screen.getByText('Geen airco')).toBeInTheDocument();
    expect(screen.getByText('20,6°')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Airco koppelen' })).toHaveAttribute('href', '/installatie');
    expect(screen.getByRole('link', { name: 'Naar Apparaten' })).toHaveAttribute('href', '/apparaten');
    expect(screen.queryByRole('button', { name: /Doeltemperatuur/ })).not.toBeInTheDocument();
    expect(container.querySelector('.note.error, .error-text')).toBeNull();
  });

  it('marks an offline unit as a warning, not as an error', () => {
    mockApi(() => ({ body: [] }));
    const z = zone({ unit_status: 'offline', online: false, setpoint_adjustable: false, notice: 'Airco niet bereikbaar. Gebruik zo nodig de afstandsbediening.', notice_level: 'warn' });
    const { container } = render(<Providers><ZoneControlPanel zone={z} /></Providers>);
    expect(screen.getByText('Airco niet bereikbaar. Gebruik zo nodig de afstandsbediening.')).toHaveClass('note', 'warn');
    expect(screen.getByRole('button', { name: 'Verwarmen' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Doeltemperatuur hoger' })).toHaveAccessibleDescription(/niet bereikbaar/);
    expect(container.querySelector('.note.error')).toBeNull();
  });
});

describe('viewers', () => {
  it('see values but no controls', () => {
    mockApi(() => ({ body: [] }));
    render(<Providers user={viewer}><ZoneControlPanel zone={off()} /><ZoneCard zone={off()} /></Providers>);
    expect(screen.queryByRole('button', { name: /Doeltemperatuur/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Verwarmen' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Terug naar schema' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Kies eerst een modus/)).not.toBeInTheDocument();
    expect(screen.getAllByText('21,0°').length).toBeGreaterThan(0);
  });
});
