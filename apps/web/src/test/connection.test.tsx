import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { emitConnectionEvent } from '../api/client';
import { qk } from '../api/keys';
import type { AuthStatus } from '../api/types';
import { addFailure, ConnectionProvider, isDown, NO_FAILURES, OFFLINE_AFTER_MS, PendingProvider, useCanAct, useConnection } from '../state';
import { classifyClose, LiveConnection, useLiveUpdates, type LiveHandlers } from '../ws';
import { OfflineBanner } from '../components/OfflineBanner';
import { FakeWebSocket, mockApi } from './helpers';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWebSocket.reset();
  localStorage.clear();
});

describe('offline threshold (item 4)', () => {
  it('is down only after 3 consecutive failures or 15 s since the first one', () => {
    let s = NO_FAILURES;
    s = addFailure(s, 1000);
    s = addFailure(s, 2000);
    expect(isDown(s, 2000)).toBe(false);
    expect(isDown(s, 1000 + OFFLINE_AFTER_MS - 1)).toBe(false);
    expect(isDown(s, 1000 + OFFLINE_AFTER_MS)).toBe(true);
    expect(isDown(addFailure(s, 3000), 3000)).toBe(true);
    expect(isDown(NO_FAILURES, 10_000_000)).toBe(false);
  });

  function Probe() {
    const c = useConnection();
    const canAct = useCanAct();
    return (
      <>
        <OfflineBanner />
        <span data-testid="state">{c.offline ? 'offline' : 'online'} {canAct ? 'act' : 'noact'}</span>
        <button onClick={() => c.wsFailed()}>wsfail</button>
        <button onClick={() => c.wsOpened()}>wsopen</button>
      </>
    );
  }

  it('API failures: 2 failures stay online, the 3rd goes offline, a success resets', () => {
    render(<ConnectionProvider><Probe /></ConnectionProvider>);
    act(() => emitConnectionEvent({ type: 'unreachable' }));
    act(() => emitConnectionEvent({ type: 'unreachable' }));
    expect(screen.getByTestId('state')).toHaveTextContent('online act');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    act(() => emitConnectionEvent({ type: 'unreachable' }));
    expect(screen.getByTestId('state')).toHaveTextContent('offline noact');
    expect(screen.getByRole('status')).toHaveTextContent('Geen verbinding met de server');
    act(() => emitConnectionEvent({ type: 'ok' }));
    expect(screen.getByTestId('state')).toHaveTextContent('online act');
  });

  it('one WS failure turns offline after 15 s without recovery', () => {
    vi.useFakeTimers();
    render(<ConnectionProvider><Probe /></ConnectionProvider>);
    act(() => screen.getByText('wsfail').click());
    expect(screen.getByTestId('state')).toHaveTextContent('online');
    act(() => vi.advanceTimersByTime(OFFLINE_AFTER_MS - 1000));
    expect(screen.getByTestId('state')).toHaveTextContent('online');
    act(() => vi.advanceTimersByTime(1100));
    expect(screen.getByTestId('state')).toHaveTextContent('offline');
    act(() => screen.getByText('wsopen').click());
    expect(screen.getByTestId('state')).toHaveTextContent('online');
  });

  it('api_unavailable is "starting", not offline, but disables actions', () => {
    render(<ConnectionProvider><Probe /></ConnectionProvider>);
    act(() => emitConnectionEvent({ type: 'starting', message: 'De app start op of wordt bijgewerkt.' }));
    expect(screen.getByTestId('state')).toHaveTextContent('online noact');
    expect(screen.getByRole('status')).toHaveTextContent('De app start op of wordt bijgewerkt.');
  });
});

describe('LiveConnection close codes', () => {
  function handlers() {
    return {
      onOpen: vi.fn(),
      onMessage: vi.fn(),
      onHello: vi.fn(),
      onFailure: vi.fn(),
      onReauth: vi.fn(),
      onRevoked: vi.fn(),
    } satisfies LiveHandlers;
  }
  const opts = { WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket, backoff: () => 1000, keepaliveMs: 0 };

  it('classifies 4401 / 4403 / others', () => {
    expect(classifyClose(4401)).toBe('reauth');
    expect(classifyClose(4403)).toBe('revoked');
    expect(classifyClose(1006)).toBe('failure');
    expect(classifyClose(1000)).toBe('failure');
  });

  it('4401 calls onReauth with the error sent before closing, and does not reconnect by itself', () => {
    vi.useFakeTimers();
    const h = handlers();
    const live = new LiveConnection('ws://x/api/v1/ws', h, opts);
    live.start();
    const ws = FakeWebSocket.last();
    ws.open();
    ws.message({ type: 'hello', data: { user: { id: 'u', username: 'a', role: 'admin' }, via: 'cookie', client_role: null, can_control: true } });
    ws.message({ type: 'error', data: { code: 'auth_required', reason: 'password_changed' } });
    ws.serverClose(4401);
    expect(h.onHello).toHaveBeenCalledTimes(1);
    expect(h.onReauth).toHaveBeenCalledWith({ code: 'auth_required', reason: 'password_changed' });
    expect(h.onFailure).not.toHaveBeenCalled();
    vi.advanceTimersByTime(120_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    live.retryIn(500);
    vi.advanceTimersByTime(600);
    expect(FakeWebSocket.instances).toHaveLength(2);
    live.stop();
  });

  it('4403 calls onRevoked and stops for good', () => {
    vi.useFakeTimers();
    const h = handlers();
    const live = new LiveConnection('ws://x', h, opts);
    live.start();
    FakeWebSocket.last().message({ type: 'error', data: { code: 'token_revoked', reason: 'phone_revoked' } });
    FakeWebSocket.last().serverClose(4403);
    expect(h.onRevoked).toHaveBeenCalledWith({ code: 'token_revoked', reason: 'phone_revoked' });
    live.retryIn(10);
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('other closes count as failures, reconnect with backoff and report reconnected=true', () => {
    vi.useFakeTimers();
    const h = handlers();
    const live = new LiveConnection('ws://x', h, opts);
    live.start();
    FakeWebSocket.last().open();
    expect(h.onOpen).toHaveBeenLastCalledWith(false);
    FakeWebSocket.last().serverClose(1006);
    expect(h.onFailure).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    FakeWebSocket.last().open();
    expect(h.onOpen).toHaveBeenLastCalledWith(true);
    FakeWebSocket.last().message({ type: 'zone.updated', data: { id: 'z1' } });
    FakeWebSocket.last().message('pong');
    expect(h.onMessage).toHaveBeenCalledTimes(1);
    live.stop();
  });
});

describe('useLiveUpdates', () => {
  beforeEach(() => vi.stubGlobal('WebSocket', FakeWebSocket));

  function Shell() {
    useLiveUpdates('h1');
    const c = useConnection();
    const auth = useQuery<AuthStatus>({ queryKey: qk.auth, queryFn: () => new Promise(() => {}), enabled: false });
    return (
      <>
        <OfflineBanner />
        <span data-testid="out">{c.wsState}|{c.offline ? 'offline' : 'online'}|{auth.data?.user ? 'in' : 'out'}|{c.authNotice?.reason ?? ''}</span>
      </>
    );
  }
  function renderShell(sessionValid: boolean) {
    mockApi((_m, url) => (url.endsWith('/auth/status') ? { body: { setup_required: false, user: sessionValid ? { id: 'u1', username: 'a', role: 'admin' } : null } } : undefined));
    const qc = new QueryClient();
    qc.setQueryData(qk.auth, { setup_required: false, user: { id: 'u1', username: 'a', role: 'admin' } });
    render(
      <QueryClientProvider client={qc}>
        <ConnectionProvider>
          <PendingProvider>
            <Shell />
          </PendingProvider>
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    return qc;
  }

  it('4401 with an ended session goes to "log in again", not offline', async () => {
    renderShell(false);
    act(() => FakeWebSocket.last().open());
    act(() => {
      FakeWebSocket.last().message({ type: 'error', data: { code: 'auth_required', reason: 'logout' } });
      FakeWebSocket.last().serverClose(4401);
    });
    await waitFor(() => expect(screen.getByTestId('out')).toHaveTextContent('idle|online|out|logout'));
    expect(screen.queryByText(/Geen verbinding/)).not.toBeInTheDocument();
  });

  it('4401 while the session is still valid: live updates refused, not offline, retried later', async () => {
    renderShell(true);
    act(() => FakeWebSocket.last().open());
    act(() => {
      FakeWebSocket.last().message({ type: 'error', data: { code: 'auth_required', reason: 'origin_mismatch' } });
      FakeWebSocket.last().serverClose(4401);
    });
    await waitFor(() => expect(screen.getByTestId('out')).toHaveTextContent('denied|online|in|'));
    expect(screen.getByRole('status')).toHaveTextContent('Live-updates zijn nu niet beschikbaar');
  });

  it('4403 shows the revoked notice on the login page path', async () => {
    renderShell(true);
    act(() => FakeWebSocket.last().open());
    act(() => {
      FakeWebSocket.last().message({ type: 'error', data: { code: 'token_revoked', reason: 'phone_revoked' } });
      FakeWebSocket.last().serverClose(4403);
    });
    await waitFor(() => expect(screen.getByTestId('out')).toHaveTextContent('idle|online|out|phone_revoked'));
  });

  it('a dropped connection is not offline before the threshold', () => {
    renderShell(true);
    act(() => FakeWebSocket.last().open());
    act(() => FakeWebSocket.last().serverClose(1006));
    expect(screen.getByTestId('out')).toHaveTextContent('closed|online|in');
  });
});
