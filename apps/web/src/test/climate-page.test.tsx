import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClimatePage, watchdogAlert, watchdogAlertTitle } from '../pages/Climate';
import { applyWsMessage } from '../ws';
import type { Alert } from '../api/types';
import { resetCommands } from '../commands';
import { mockApi, newClient, Providers } from './helpers';
import { zone } from './fixtures';

const HOMES = [{ id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true }];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  resetCommands();
});

const watchdog = (over: Partial<Alert> = {}): Alert => ({
  id: 'a-wd',
  severity: 'warn',
  title: 'Woonkamer: temperatuur reageert niet',
  detail: 'De airco verwarmt al 60 minuten, maar de temperatuur ging van 18.0 naar 18.1 °C (doel 21.0 °C). Controleer ramen en deuren, het filter en of de airco echt draait.',
  created_at: '2026-11-09T07:30:00Z',
  acknowledged: false,
  source: 'climate',
  ...over,
});

function climateApi(alerts: Alert[], zones = [zone({ watchdog_since: '2026-11-09T08:15:00Z' })]) {
  return mockApi((_m, url) => {
    if (url.endsWith('/homes')) return { body: HOMES };
    if (url.endsWith('/homes/h1/zones')) return { body: zones };
    if (url.endsWith('/homes/h1/alerts')) return { body: alerts };
    if (url.endsWith('/schedule')) return { body: { blocks: [], profile_id: null, profile: null } };
    if (url.includes('/history')) return { body: { temperature: [], target: [] } };
    return { body: [] };
  });
}

describe('watchdog note (item 45)', () => {
  it('finds only the open watchdog alert of this zone', () => {
    const other = watchdog({ id: 'a2', title: watchdogAlertTitle('Woonkamer 2') });
    const command = watchdog({ id: 'a3', title: 'Airco Woonkamer voert commando niet uit' });
    expect(watchdogAlert([other, command], { name: 'Woonkamer' })).toBeUndefined();
    expect(watchdogAlert([other, watchdog()], { name: 'Woonkamer' })?.id).toBe('a-wd');
    expect(watchdogAlert([watchdog({ source: 'zigbee' })], { name: 'Woonkamer' })).toBeUndefined();
    expect(watchdogAlert(undefined, { name: 'Woonkamer' })).toBeUndefined();
  });

  it('says nothing during a normal heat-up: watchdog_since alone is only the start of a watchdog window', async () => {
    const calls = climateApi([]);
    render(<Providers route="/klimaat"><ClimatePage /></Providers>);
    expect(await screen.findByRole('heading', { name: 'Schema vandaag' })).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/homes/h1/alerts'))).toBe(true));
    expect(screen.queryByTestId('watchdog-note')).not.toBeInTheDocument();
    expect(screen.queryByText(/reageert niet/)).not.toBeInTheDocument();
  });

  it('warns while the watchdog alert is open, with its time and details, and stops when it is resolved', async () => {
    climateApi([watchdog()]);
    const client = newClient();
    render(<Providers route="/klimaat" client={client}><ClimatePage /></Providers>);
    const note = await screen.findByTestId('watchdog-note');
    expect(note).toHaveTextContent('Waakhond: Woonkamer reageert niet op de airco (gemeld 9 nov, 08:30).');
    expect(note).toHaveTextContent('ging van 18.0 naar 18.1 °C (doel 21.0 °C)');
    act(() => applyWsMessage(client, 'h1', { type: 'alert.resolved', data: { id: 'a-wd' } }));
    await waitFor(() => expect(screen.queryByTestId('watchdog-note')).not.toBeInTheDocument());
  });
});

describe('zone tabs (toegankelijkheid)', () => {
  it('the selected zone tab controls the zone panel, which is labelled by it', async () => {
    climateApi([], [zone(), zone({ id: 'z2', name: 'Slaapkamer', room: 'Slaapkamer' })]);
    render(<Providers route="/klimaat"><ClimatePage /></Providers>);
    const tabs = await screen.findByRole('tablist', { name: 'Zone' });
    const living = within(tabs).getByRole('tab', { name: 'Woonkamer' });
    const panel = screen.getByRole('tabpanel', { name: 'Woonkamer' });
    expect(living).toHaveAttribute('aria-controls', panel.id);
    expect(panel).not.toHaveAttribute('tabindex'); // starts with the zone's own tablist
    expect(within(panel).getByRole('tablist', { name: 'Onderdelen van Woonkamer' })).toBeInTheDocument();

    await userEvent.setup().click(within(tabs).getByRole('tab', { name: 'Slaapkamer' }));
    const bedroom = within(tabs).getByRole('tab', { name: 'Slaapkamer' });
    expect(bedroom).toHaveAttribute('aria-controls', screen.getByRole('tabpanel', { name: 'Slaapkamer' }).id);
    expect(within(tabs).getByRole('tab', { name: 'Woonkamer' })).not.toHaveAttribute('aria-controls');
  });
});
