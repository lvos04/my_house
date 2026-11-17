// S-P3-700 (HR-1): in de herstelmodus accepteert de API de websocket en sluit hem meteen met 1013 (database_newer).
// De web-app mag daar niet in een strakke lus op raken: de backoff gaat pas na het "hello"-bericht van de server terug
// naar 0 (net als Android, Realtime.kt), een cyclus "accept + 1013" haalt de queries niet opnieuw op, en 1013 telt niet
// als "offline", zodat de uitleg van de herstelmodus zichtbaar blijft.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { ConnectionProvider, PendingProvider, useConnection } from '../state';
import { LiveConnection, useLiveUpdates, type LiveHandlers } from '../ws';
import { OfflineBanner } from '../components/OfflineBanner';
import { FakeWebSocket, mockApi } from './helpers';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWebSocket.reset();
  localStorage.clear();
});

const TEKST = 'De database is van een nieuwere my_house-versie; kies de bijpassende versie.';
const HELLO = { type: 'hello', data: { user: { id: 'u1', username: 'admin', role: 'admin' }, via: 'cookie', client_role: null, can_control: true } };
const CYCLI = 6;

function handlers(): LiveHandlers {
  return { onOpen: vi.fn(), onMessage: vi.fn(), onHello: vi.fn(), onFailure: vi.fn(), onUnavailable: vi.fn(), onReauth: vi.fn(), onRevoked: vi.fn() };
}

function liveMet(pogingen: number[], h: LiveHandlers) {
  const live = new LiveConnection('ws://x', h, {
    WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
    backoff: (n) => (pogingen.push(n), 1000),
    keepaliveMs: 0,
  });
  live.start();
  for (let i = 0; i < CYCLI; i++) {
    FakeWebSocket.last().open();
    FakeWebSocket.last().serverClose(1013);
    vi.advanceTimersByTime(1000);
  }
  return live;
}

function Shell() {
  useLiveUpdates('h1');
  const c = useConnection();
  useQuery({ queryKey: ['homes'], queryFn: api.homes, retry: false });
  useQuery({ queryKey: ['overview', 'h1'], queryFn: () => api.overview('h1'), retry: false });
  return (
    <>
      <OfflineBanner />
      <span data-testid="s">{c.wsState}|{c.offline ? 'offline' : 'online'}</span>
    </>
  );
}

/** Een tabblad met lopende queries; de API staat in de herstelmodus (503 database_newer, websocket accept + 1013). */
async function tabbladInHerstelmodus() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal('WebSocket', FakeWebSocket);
  const st = { herstel: true };
  const calls = mockApi(() => (st.herstel ? { status: 503, body: { detail: { code: 'database_newer', message: TEKST } } } : { body: [] }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const herlaad = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <ConnectionProvider>
        <PendingProvider>
          <Shell />
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50);
  });
  expect(calls).toHaveLength(2); // de eerste keer laden
  expect(screen.getByRole('status')).toHaveTextContent(`${TEKST} Bediening is even uitgeschakeld.`);
  /** Eén cyclus accept + 1013, dan ruim boven de grootste backoff (30 s) en de 15 s van de offline-regel wachten. */
  async function cyclus(): Promise<number> {
    const n = FakeWebSocket.instances.length;
    const voor = calls.length;
    act(() => FakeWebSocket.last().open());
    act(() => FakeWebSocket.last().serverClose(1013));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(n + 1);
    return calls.length - voor;
  }
  return { st, calls, herlaad, cyclus };
}

describe('websocket in de herstelmodus (accept + 1013)', () => {
  it('(a) de backoff groeit bij herhaald accept + 1013', () => {
    vi.useFakeTimers();
    const pogingen: number[] = [];
    const h = handlers();
    const live = liveMet(pogingen, h);
    expect(pogingen).toEqual([0, 1, 2, 3, 4, 5]);
    expect(h.onOpen).not.toHaveBeenCalled(); // geen "heropend" zonder hello
    expect(h.onUnavailable).toHaveBeenCalledTimes(CYCLI);
    expect(h.onFailure).not.toHaveBeenCalled(); // 1013 telt niet als storing
    live.stop();
  });

  it('(b) een cyclus accept + 1013 haalt de queries niet opnieuw op', async () => {
    const t = await tabbladInHerstelmodus();
    const perCyclus: number[] = [];
    for (let i = 0; i < CYCLI; i++) perCyclus.push(await t.cyclus());
    expect(perCyclus).toEqual([0, 0, 0, 0, 0, 0]);
    expect(t.herlaad).not.toHaveBeenCalled();
  }, 20_000);

  it('(c) na een echte hello gaat de backoff terug naar 0 en worden de queries wel opnieuw opgehaald', async () => {
    vi.useFakeTimers();
    const pogingen: number[] = [];
    const h = handlers();
    const live = liveMet(pogingen, h);
    FakeWebSocket.last().open();
    FakeWebSocket.last().message(HELLO);
    expect(h.onOpen).toHaveBeenCalledTimes(1);
    expect(h.onOpen).toHaveBeenLastCalledWith(true);
    expect(h.onHello).toHaveBeenCalledTimes(1);
    FakeWebSocket.last().serverClose(1006);
    expect(pogingen).toEqual([0, 1, 2, 3, 4, 5, 0]);
    live.stop();
    vi.useRealTimers();
    FakeWebSocket.reset();

    // In de app: de herstelmodus is voorbij, de server zegt hello -> verbonden en alles één keer opnieuw ophalen.
    const t = await tabbladInHerstelmodus();
    for (let i = 0; i < 2; i++) await t.cyclus();
    t.st.herstel = false;
    const voor = t.calls.length;
    act(() => FakeWebSocket.last().open());
    act(() => FakeWebSocket.last().message(HELLO));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(t.herlaad).toHaveBeenCalledTimes(1);
    expect(t.calls.length - voor).toBe(2);
    expect(screen.getByTestId('s')).toHaveTextContent('open|online');
    expect(screen.queryByRole('status')).toBeNull(); // de uitleg verdwijnt na een geslaagd verzoek
  }, 20_000);

  it('(d) bij 1013 geen offline-banner: de uitleg van de herstelmodus blijft staan', async () => {
    const t = await tabbladInHerstelmodus();
    for (let i = 0; i < CYCLI; i++) {
      await t.cyclus();
      expect(screen.queryByText(/Geen verbinding met de server/)).toBeNull();
      expect(screen.getByRole('status')).toHaveTextContent(`${TEKST} Bediening is even uitgeschakeld.`);
      expect(screen.getByTestId('s')).toHaveTextContent('connecting|online');
    }
  }, 20_000);
});
