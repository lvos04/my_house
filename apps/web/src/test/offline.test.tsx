import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ConnectionProvider, PendingProvider } from '../state';
import { OfflineBanner } from '../components/OfflineBanner';
import { OverviewView } from '../pages/Overview';
import { overview } from './fixtures';

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

describe('offline rule', () => {
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
