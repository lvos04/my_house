import { vi } from 'vitest';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConnectionProvider, HomeProvider, PendingProvider, type ConnectionInitial } from '../state';
import { UserContext } from '../user';
import type { User } from '../api/types';

export interface Call {
  method: string;
  url: string;
  body: unknown;
}
export type RouteResult = { status?: number; body?: unknown; headers?: Record<string, string> } | undefined;
export type Route = (method: string, url: string, body: unknown) => RouteResult;

/** Stub fetch: route(method, url, body) -> {status, body}. Unrouted calls answer 200 []. */
export function mockApi(route: Route): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const body = typeof init.body === 'string' && init.body ? JSON.parse(init.body) : undefined;
      const method = init.method ?? 'GET';
      calls.push({ method, url, body });
      const r = route(method, url, body) ?? { body: [] };
      const status = r.status ?? 200;
      return new Response(status === 204 ? null : JSON.stringify(r.body ?? {}), {
        status,
        headers: { 'content-type': 'application/json', ...(r.headers ?? {}) },
      });
    }),
  );
  return calls;
}

export const admin: User = { id: 'u1', username: 'admin', role: 'admin', is_admin: true, can_control: true };
export const viewer: User = { id: 'u2', username: 'kijker', role: 'viewer', is_admin: false, can_control: false };

export function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

/** All providers a page needs. */
export function Providers({
  children,
  user = admin,
  client = newClient(),
  route = '/',
  connection = { apiReachable: true, wsState: 'open' },
  homeId = 'h1',
}: {
  children: ReactNode;
  user?: User | null;
  client?: QueryClient;
  route?: string;
  connection?: ConnectionInitial;
  homeId?: string | null;
}) {
  if (homeId) localStorage.setItem('shc.home', homeId);
  return (
    <QueryClientProvider client={client}>
      <ConnectionProvider initial={connection}>
        <PendingProvider>
          <UserContext.Provider value={user}>
            <MemoryRouter initialEntries={[route]}>
              <HomeProvider>{children}</HomeProvider>
            </MemoryRouter>
          </UserContext.Provider>
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}

/** Minimal controllable WebSocket for tests. */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  message(msg: unknown) {
    this.onmessage?.({ data: typeof msg === 'string' ? msg : JSON.stringify(msg) });
  }
  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  static last(): FakeWebSocket {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }
  static reset() {
    FakeWebSocket.instances = [];
  }
}

/** Fake XMLHttpRequest: reports upload progress, then answers. */
export class FakeXhr {
  static next: { status: number; body: unknown } = { status: 200, body: {} };
  static last: FakeXhr | null = null;
  upload: { onprogress: ((e: { loaded: number; total: number; lengthComputable: boolean }) => void) | null } = { onprogress: null };
  headers: Record<string, string> = {};
  method = '';
  url = '';
  status = 0;
  responseText = '';
  responseURL = '';
  body: unknown = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    FakeXhr.last = this;
  }
  open(m: string, u: string) {
    this.method = m;
    this.url = u;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  getResponseHeader(k: string) {
    return k.toLowerCase() === 'content-type' ? 'application/json' : null;
  }
  abort() {
    this.onabort?.();
  }
  send(body: unknown) {
    this.body = body;
    const { status, body: res } = FakeXhr.next;
    setTimeout(() => {
      this.upload.onprogress?.({ loaded: 50, total: 100, lengthComputable: true });
      this.upload.onprogress?.({ loaded: 100, total: 100, lengthComputable: true });
      this.status = status;
      this.responseURL = 'http://localhost' + this.url;
      this.responseText = JSON.stringify(res);
      this.onload?.();
    }, 0);
  }
}
