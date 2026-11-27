import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ConnectionProvider, PendingProvider } from '../state';
import { OfflineBanner } from '../components/OfflineBanner';
import { ZoneControlPanel } from '../components/zone';
import { OverviewView } from '../pages/Overview';
import { overview, zone } from './fixtures';

function wrap(children: ReactNode, online: boolean) {
  const lastSeen = new Date(2026, 10, 8, 14, 5);
  return (
    <QueryClientProvider client={new QueryClient()}>
      <ConnectionProvider initial={{ apiReachable: online, wsState: online ? 'open' : 'closed', lastSeen }}>
        <PendingProvider>
          <MemoryRouter>
            <OfflineBanner />
            {children}
          </MemoryRouter>
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}

describe('brontijd van een sensormeting', () => {
  afterEach(() => vi.useRealTimers());

  it('toont een temperatuur met een toekomstige brontijd niet als actueel', () => {
    const kamer = zone();
    kamer.temperature.at = new Date(Date.now() + 86_400_000).toISOString();
    render(wrap(<ZoneControlPanel zone={kamer} />, true));
    expect(screen.queryByText(/^nu\s/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Laatst bekend.*20,6°/i)).toBeVisible();
  });

  it('laat elke meting op haar eigen termijn verlopen zonder nieuwe zonegegevens', () => {
    vi.useFakeTimers();
    const nu = Date.now();
    const kamer = zone();
    kamer.policy = { ...kamer.policy, stale_after_s: 300 };
    kamer.temperature = { ...kamer.temperature, at: new Date(nu - 295_000).toISOString() };
    kamer.humidity = { ...kamer.humidity, at: new Date(nu - 285_000).toISOString() };
    render(wrap(<ZoneControlPanel zone={kamer} />, true));
    expect(screen.getByText('nu 20,6°')).toBeVisible();
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByText('nu 20,6°')).toBeVisible();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText(/^nu\s/i)).not.toBeInTheDocument();
    expect(screen.getByText('54% RV')).toBeVisible();
    expect(screen.queryByRole('group', { name: 'Laatst bekende luchtvochtigheid' })).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(10_000));
    expect(screen.getByRole('group', { name: 'Laatst bekende luchtvochtigheid' })).toHaveTextContent('54% RV');
  });
});

describe('offline rule', () => {
  it('houdt de laatst bekende waarden bij hun eigen meettijd in een waardevak', () => {
    const kamer = zone();
    render(wrap(<ZoneControlPanel zone={kamer} />, false));
    for (const [naam, waarde, tijd] of [
      ['temperatuur', '20,6°', kamer.temperature.at],
      ['luchtvochtigheid', '54% RV', kamer.humidity.at],
    ] as const) {
      const vak = screen.getByRole('group', { name: `Laatst bekende ${naam}` });
      expect(vak).toHaveTextContent(waarde);
      expect(vak.querySelector('time')).toHaveAttribute('datetime', tijd);
    }
  });

  it('toont een eerder verse temperatuur zonder verbinding als laatst bekende meting', () => {
    const kamer = zone();
    render(wrap(<ZoneControlPanel zone={kamer} tz="Europe/Amsterdam" />, false));
    const bediening = within(screen.getByRole('region', { name: 'Bediening' }));
    expect(bediening.queryByText(/^nu\s/i)).not.toBeInTheDocument();
    expect(bediening.getByText('Verouderd')).toBeVisible();
    const meting = bediening.getByText(/Laatst bekend.*20,6°/i);
    expect(meting).toBeVisible();
    expect(meting.querySelector('time')).toHaveAttribute('datetime', kamer.temperature.at);
  });

  it('shows last-known banner and disables all action buttons when offline', () => {
    render(wrap(<OverviewView data={overview} />, false));
    expect(screen.getByRole('status')).toHaveTextContent('laatst bekend 14:05');
    expect(screen.getByRole('button', { name: /Doeltemperatuur Woonkamer hoger/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Doeltemperatuur Woonkamer lager/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Keuken/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Melding Sensor verouderd afhandelen/ })).toBeDisabled();
    // Last known data is still shown.
    expect(screen.getByText('20,6°')).toBeInTheDocument();
  });

  it('enables actions and hides the banner when online', () => {
    render(wrap(<OverviewView data={overview} />, true));
    expect(screen.queryByText(/Geen verbinding/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Doeltemperatuur Woonkamer hoger/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Keuken/ })).toBeEnabled();
  });
});
