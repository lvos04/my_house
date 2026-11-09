import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { OverviewPage, OverviewView, attentionItems } from '../pages/Overview';
import { applyWsMessage } from '../ws';
import { applyCommandUpdate, resetCommands } from '../commands';
import type { Overview, StatusCheck } from '../api/types';
import { admin, mockApi, newClient, Providers, viewer } from './helpers';
import { group, overview } from './fixtures';

const worker: StatusCheck = { key: 'worker', label: 'Achtergronddienst', state: 'error', detail: 'Geen hartslag: de achtergronddienst draait niet.', age_s: 900 };
const safe: StatusCheck = { key: 'safe_mode', label: 'Veilige modus', state: 'warn', detail: "Geen apparaatcommando's tot je de koppelingen bevestigt." };
const backup: StatusCheck = { key: 'backup', label: 'Back-up', state: 'warn', detail: 'Laatste back-up 3 dagen geleden', age_h: 72 };
const backupOk: StatusCheck = { key: 'backup', label: 'Back-up', state: 'ok', detail: 'Laatste back-up 2 uur geleden', age_h: 2 };

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  resetCommands();
});

describe('Overzicht (items 22, 31, 36)', () => {
  it('lists worker heartbeat, safe mode and old backups at the top, with links for admins', () => {
    expect(attentionItems([worker, safe, backupOk]).map((s) => s.key)).toEqual(['worker', 'safe_mode']);
    mockApi(() => ({ body: [] }));
    render(<Providers user={admin}><OverviewView data={{ ...overview, status: [worker, safe, backup, ...overview.status] }} /></Providers>);
    // Peil-microcopy (PEIL_MIGRATIE §10): kop "Storing in het systeem" bij een storing, anders "Wat actie nodig heeft".
    const box = screen.getByRole('region', { name: 'Storing in het systeem' });
    expect(within(box).getAllByText('Storing').length).toBeGreaterThan(0); // StatusBadge: icoon + vast woord
    expect(within(box).getByText(/Geen hartslag/)).toBeInTheDocument();
    expect(within(box).getByRole('link', { name: 'Veilige modus bekijken' })).toHaveAttribute('href', '/systeem#veilige-modus');
    expect(within(box).getByRole('link', { name: 'Naar back-ups' })).toHaveAttribute('href', '/systeem#backups');
  });

  it('shows nothing extra when everything is fine', () => {
    mockApi(() => ({ body: [] }));
    render(<Providers><OverviewView data={{ ...overview, status: [backupOk, ...overview.status] }} /></Providers>);
    expect(screen.queryByRole('region', { name: /Storing in het systeem|Wat actie nodig heeft/ })).not.toBeInTheDocument();
  });

  it('viewers see the state but no controls', () => {
    mockApi(() => ({ body: [] }));
    render(<Providers user={viewer}><OverviewView data={{ ...overview, status: [worker] }} /></Providers>);
    expect(screen.queryByRole('button', { name: /Doeltemperatuur/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Keuken/ })).not.toBeInTheDocument();
    expect(screen.getByText('Keuken')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /afhandelen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Naar Systeem' })).not.toBeInTheDocument();
    expect(screen.getByText(/Geen hartslag/)).toBeInTheDocument();
  });

  it('follows energy.live and alert.resolved from the live connection', async () => {
    mockApi((_m, url) => (url.endsWith('/homes/h1/overview') ? { body: overview } : { body: [] }));
    const client = newClient();
    render(<Providers client={client}><OverviewPage /></Providers>);
    expect(await screen.findByText('Sensor verouderd')).toBeInTheDocument();
    act(() => {
      applyWsMessage(client, 'h1', { type: 'energy.live', data: { at: new Date().toISOString(), total_w: 1234, complete: false, note: 'Boiler-meting ontbreekt; overig is geschat.', parts: [] } });
      applyWsMessage(client, 'h1', { type: 'alert.resolved', data: { id: 'a1' } });
    });
    expect(await screen.findByText('1.234 W')).toBeInTheDocument();
    expect(screen.getByText('Boiler-meting ontbreekt; overig is geschat.')).toBeInTheDocument();
    expect(screen.getByText('geschat')).toBeInTheDocument();
    expect(screen.queryByText('Sensor verouderd')).not.toBeInTheDocument();
    expect(screen.getByText('Geen open meldingen.')).toBeInTheDocument();
    expect((client.getQueryData(['overview', 'h1']) as Overview).power_now?.total_w).toBe(1234);
  });

  it('tracks a light toggle until the bridge confirms it, and shows a failure', async () => {
    const calls = mockApi((_m, url) => (url.endsWith('/lights/groups/g1') ? { status: 202, body: { command_id: 'l1', status: 'queued' } } : { body: [] }));
    render(<Providers><OverviewView data={{ ...overview, lights: [group(), group({ id: 'all', name: 'Alle lampen', all_lights: true, hidden: true })] }} /></Providers>);
    expect(screen.queryByRole('button', { name: /Alle lampen/ })).not.toBeInTheDocument();
    expect(screen.getByText('2 van 2 aan')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Keuken/ }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/lights/groups/g1'))?.body).toEqual({ on: false }));
    expect(await screen.findByText('Uit · wordt verzonden…')).toBeInTheDocument();
    act(() => applyCommandUpdate({ id: 'l1', status: 'failed', detail: 'Hue Bridge meldt: lamp niet bereikbaar' }));
    expect(screen.getByText('Mislukt: Hue Bridge meldt: lamp niet bereikbaar')).toBeInTheDocument();
  });
});
