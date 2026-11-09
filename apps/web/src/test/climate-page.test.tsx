import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClimatePage, watchdogAlert, watchdogAlertKey, watchdogAlertTitle } from '../pages/Climate';
import { applyWsMessage } from '../ws';
import { qk } from '../api/keys';
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
  key: 'watchdog:z1',
  ...over,
});
/** The same alert from a server that does not send the alert key yet. */
const legacyWatchdog = (over: Partial<Alert> = {}): Alert => {
  const { key: _key, ...rest } = watchdog(over);
  return rest;
};

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
  const living = { id: 'z1', name: 'Woonkamer' };

  it('finds the open watchdog alert of this zone by its key "watchdog:{zone.id}"', () => {
    expect(watchdogAlertKey('z1')).toBe('watchdog:z1');
    const other = watchdog({ id: 'a2', key: 'watchdog:z2', title: watchdogAlertTitle('Woonkamer 2') });
    const command = watchdog({ id: 'a3', key: 'command:u1', title: 'Airco Woonkamer voert commando niet uit' });
    expect(watchdogAlert([other, command], living)).toBeUndefined();
    expect(watchdogAlert([other, command, watchdog()], living)?.id).toBe('a-wd');
    expect(watchdogAlert(undefined, living)).toBeUndefined();
    expect(watchdogAlert(null, living)).toBeUndefined();
  });

  it('the key wins over the title: a renamed zone still finds its alert, a namesake zone does not take it', () => {
    // Alert raised before the zone was renamed: the title still has the old name.
    const raised = watchdog({ title: watchdogAlertTitle('Zitkamer') });
    expect(watchdogAlert([raised], living)?.id).toBe('a-wd');
    // Two zones called "Woonkamer" (e.g. two homes' zones in one list): only the key tells them apart.
    const namesake = watchdog({ id: 'a-z9', key: 'watchdog:z9' });
    expect(watchdogAlert([namesake], living)).toBeUndefined();
    expect(watchdogAlert([namesake], { id: 'z9', name: 'Woonkamer' })?.id).toBe('a-z9');
  });

  it('falls back to source + title for alerts without a key (older server)', () => {
    expect(watchdogAlert([legacyWatchdog()], living)?.id).toBe('a-wd');
    expect(watchdogAlert([legacyWatchdog({ title: watchdogAlertTitle('Woonkamer 2') })], living)).toBeUndefined();
    expect(watchdogAlert([legacyWatchdog({ source: 'zigbee' })], living)).toBeUndefined();
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

  it('shows the note for an alert raised under the old zone name, and for an older server without keys', async () => {
    climateApi([watchdog({ title: watchdogAlertTitle('Zitkamer') })]);
    const { unmount } = render(<Providers route="/klimaat"><ClimatePage /></Providers>);
    expect(await screen.findByTestId('watchdog-note')).toHaveTextContent('Waakhond: Woonkamer reageert niet op de airco');
    unmount();

    climateApi([legacyWatchdog()]);
    render(<Providers route="/klimaat"><ClimatePage /></Providers>);
    expect(await screen.findByTestId('watchdog-note')).toHaveTextContent('Waakhond: Woonkamer reageert niet op de airco');
  });

  it('a live alert.created with the zone key shows the note without a reload', async () => {
    const calls = climateApi([]);
    const client = newClient();
    render(<Providers route="/klimaat" client={client}><ClimatePage /></Providers>);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/homes/h1/alerts'))).toBe(true));
    await waitFor(() => expect(client.getQueryData(qk.alerts('h1'))).toBeDefined());
    act(() => applyWsMessage(client, 'h1', { type: 'alert.created', data: watchdog() }));
    expect(await screen.findByTestId('watchdog-note')).toBeInTheDocument();
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
