// S-P3-700 (EX15): de API staat in de herstelmodus (database van een nieuwere my_house-versie) en geeft op elke
// vraag 503 database_newer met een vaste Nederlandse uitleg. De web-app heeft daar geen eigen scherm voor (besluit:
// geen nieuw scherm); de bestaande startkaart en de statusregel tonen de tekst van de server, net als bij
// api_unavailable, in plaats van alleen "Controleer of de app op de NAS draait".
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { App } from '../App';
import { FakeWebSocket } from './helpers';

afterEach(() => {
  vi.unstubAllGlobals();
  FakeWebSocket.reset();
  localStorage.clear();
});

const SERVERTEKST =
  'De database is van een nieuwere my_house-versie; kies in het formulier van de Custom App de bijpassende versie ' +
  'of zet de snapshot van vóór de update terug. Deze versie verandert niets aan de database.';

describe('herstelmodus van de API (503 database_newer)', () => {
  it('de startkaart en de statusregel tonen de tekst van de server', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { code: 'database_newer', message: SERVERTEKST } }), {
          status: 503,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
    render(<App client={client} />);

    expect(await screen.findByText(SERVERTEKST)).toBeInTheDocument(); // de kaart: precies de servertekst
    expect(screen.getByText(`${SERVERTEKST} Bediening is even uitgeschakeld.`)).toBeInTheDocument(); // de statusregel
    expect(screen.queryByText('De server is niet bereikbaar. Controleer of de app op de NAS draait.')).toBeNull();
    expect(fetchMock).toHaveBeenCalled();
  });
});
