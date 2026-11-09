import { useEffect, useRef } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { api, API_BASE } from './api/client';
import { qk } from './api/keys';
import type { Alert, AuthStatus, HelloData, LightGroup, Lights, LivePower, Overview, StatusCheck, WsErrorData, WsMessage, Zone } from './api/types';
import { applyCommandSnapshot, applyCommandUpdate } from './commands';
import { useConnection, usePending } from './state';

export function wsUrl(homeId: string, loc: Pick<Location, 'protocol' | 'host'> = window.location): string {
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${loc.host}${API_BASE}/ws?home_id=${encodeURIComponent(homeId)}`;
}

/** Exponential backoff with jitter, capped at 30 s. */
export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(30_000, 1000 * 2 ** attempt);
  return Math.round(base / 2 + rand() * (base / 2));
}

/** Server close codes: 4401 = log in again (web session ended), 4403 = this device's access was revoked. */
export const CLOSE_AUTH_REQUIRED = 4401;
export const CLOSE_REVOKED = 4403;
export type CloseKind = 'reauth' | 'revoked' | 'failure';
export function classifyClose(code: number): CloseKind {
  if (code === CLOSE_AUTH_REQUIRED) return 'reauth';
  if (code === CLOSE_REVOKED) return 'revoked';
  return 'failure';
}
/** Retry interval while live updates are refused but the session itself is valid. */
export const DENIED_RETRY_MS = 60_000;
const KEEPALIVE_MS = 25_000;

/* ---------- cache updates ---------- */

function patchOverview(qc: QueryClient, homeId: string, fn: (o: Overview) => Overview) {
  qc.setQueryData<Overview>(qk.overview(homeId), (o) => (o ? fn(o) : o));
}

/** Apply one live message to the React Query cache (and the command store). Unknown types are ignored. */
export function applyWsMessage(qc: QueryClient, homeId: string, msg: WsMessage) {
  switch (msg.type) {
    case 'zone.updated': {
      const z: Zone = msg.data;
      qc.setQueryData(qk.zone(z.id), z);
      qc.setQueryData<Zone[]>(qk.zones(homeId), (zs) => zs?.map((x) => (x.id === z.id ? z : x)));
      patchOverview(qc, homeId, (o) => ({ ...o, zones: o.zones.map((x) => (x.id === z.id ? z : x)) }));
      applyCommandSnapshot(`zone:${z.id}`, z.pending_command ?? null);
      break;
    }
    case 'lights.updated': {
      const g: LightGroup = msg.data;
      qc.setQueryData<Lights | undefined>(qk.lights(homeId), (l) => (l ? { ...l, groups: l.groups.map((x) => (x.id === g.id ? g : x)) } : l));
      patchOverview(qc, homeId, (o) => ({ ...o, lights: o.lights.map((x) => (x.id === g.id ? g : x)) }));
      applyCommandSnapshot(`group:${g.id}`, g.last_command ?? null);
      break;
    }
    case 'energy.live': {
      const p: LivePower = msg.data;
      qc.setQueryData(qk.energyLive(homeId), p);
      patchOverview(qc, homeId, (o) => ({ ...o, power_now: p }));
      break;
    }
    case 'alert.created': {
      const a: Alert = msg.data;
      patchOverview(qc, homeId, (o) => ({ ...o, alerts: [a, ...o.alerts.filter((x) => x.id !== a.id)] }));
      qc.setQueryData<Alert[]>(qk.alerts(homeId), (l) => (l ? [a, ...l.filter((x) => x.id !== a.id)] : l));
      break;
    }
    case 'alert.resolved': {
      const id = msg.data.id;
      patchOverview(qc, homeId, (o) => ({ ...o, alerts: o.alerts.filter((x) => x.id !== id) }));
      qc.setQueryData<Alert[]>(qk.alerts(homeId), (l) => l?.filter((x) => x.id !== id));
      break;
    }
    case 'status.updated': {
      const s: StatusCheck[] = msg.data;
      patchOverview(qc, homeId, (o) => ({ ...o, status: s }));
      qc.invalidateQueries({ queryKey: qk.system });
      break;
    }
    case 'command.queued':
      applyCommandUpdate({ id: msg.data.id, status: 'queued' });
      break;
    case 'command.updated':
      applyCommandUpdate(msg.data);
      break;
    case 'integration.changed':
      qc.invalidateQueries({ queryKey: qk.integrations });
      qc.invalidateQueries({ queryKey: qk.system });
      break;
    case 'refresh':
      // The real event was too large to send: fetch everything again.
      qc.invalidateQueries();
      break;
    default:
      break; // hello/error are handled by LiveConnection; anything else (e.g. auth.revoked) is ignored
  }
}

/* ---------- connection ---------- */

export interface LiveHandlers {
  /** `reconnected`: messages may have been missed, refetch. */
  onOpen: (reconnected: boolean) => void;
  onMessage: (msg: WsMessage) => void;
  onHello: (h: HelloData) => void;
  /** Connection lost or refused for another reason (counts towards "offline"). */
  onFailure: () => void;
  /** Close 4401. Call conn.retryIn(ms) to try again (e.g. when the HTTP session is still valid). */
  onReauth: (err: WsErrorData | null) => void;
  /** Close 4403: access revoked; no reconnect. */
  onRevoked: (err: WsErrorData | null) => void;
}
type WsCtor = new (url: string) => WebSocket;
export interface LiveOptions {
  WebSocketImpl?: WsCtor;
  backoff?: (attempt: number) => number;
  keepaliveMs?: number;
}

/** One live connection with reconnects. Framework-free so the close-code rules can be tested. */
export class LiveConnection {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private everOpened = false;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private keepalive: ReturnType<typeof setInterval> | undefined;
  private lastError: WsErrorData | null = null;
  private readonly WS: WsCtor;
  private readonly backoff: (attempt: number) => number;

  constructor(
    private readonly url: string,
    private readonly h: LiveHandlers,
    private readonly opts: LiveOptions = {},
  ) {
    this.WS = opts.WebSocketImpl ?? WebSocket;
    this.backoff = opts.backoff ?? ((a) => backoffMs(a));
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    clearInterval(this.keepalive);
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close();
    } catch {
      /* ignore */
    }
  }

  /** Reconnect after `ms` (used after a 4401 when the session turned out to be fine). */
  retryIn(ms: number) {
    if (this.stopped) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), ms);
  }

  /** Reconnect now if not connected (tab visible again, network back). */
  nudge() {
    if (this.stopped || (this.ws && this.ws.readyState <= 1)) return;
    clearTimeout(this.timer);
    this.connect();
  }

  private connect() {
    if (this.stopped) return;
    this.lastError = null;
    let ws: WebSocket;
    try {
      ws = new this.WS(this.url);
    } catch {
      this.h.onFailure();
      this.retryIn(this.backoff(this.attempt++));
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      const reconnected = this.everOpened;
      this.everOpened = true;
      this.attempt = 0;
      clearInterval(this.keepalive);
      const every = this.opts.keepaliveMs ?? KEEPALIVE_MS;
      if (every > 0)
        this.keepalive = setInterval(() => {
          try {
            ws.send('ping');
          } catch {
            /* closing */
          }
        }, every);
      this.h.onOpen(reconnected);
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (this.ws !== ws) return;
      let msg: WsMessage;
      try {
        msg = JSON.parse(ev.data as string) as WsMessage;
      } catch {
        return; // "pong"
      }
      if (!msg || typeof msg !== 'object') return;
      if (msg.type === 'error') {
        this.lastError = msg.data;
        return;
      }
      if (msg.type === 'hello') {
        this.h.onHello(msg.data);
        return;
      }
      this.h.onMessage(msg);
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws) return;
      this.ws = null;
      clearInterval(this.keepalive);
      if (this.stopped) return;
      const kind = classifyClose(ev.code);
      if (kind === 'reauth') return this.h.onReauth(this.lastError);
      if (kind === 'revoked') {
        this.stopped = true;
        return this.h.onRevoked(this.lastError);
      }
      this.h.onFailure();
      this.retryIn(this.backoff(this.attempt++));
    };
  }
}

/** Live updates for the selected home. Reconnects with backoff, refetches after a reconnect, treats
 * close 4401 as "log in again" (or "live updates refused" when the session is still fine) and 4403 as
 * "access revoked" — neither counts as offline. */
export function useLiveUpdates(homeId: string | null) {
  const qc = useQueryClient();
  const conn = useConnection();
  const { confirm } = usePending();
  const ref = useRef({ conn, confirm });
  ref.current = { conn, confirm };

  useEffect(() => {
    if (!homeId || typeof WebSocket === 'undefined') return;
    const c = () => ref.current.conn;
    const toLogin = (code: string, reason?: string) => {
      c().setAuthNotice({ code, reason });
      qc.setQueryData<AuthStatus>(qk.auth, (s) => ({ setup_required: s?.setup_required ?? false, user: null }));
    };
    const live: LiveConnection = new LiveConnection(wsUrl(homeId), {
      onOpen: (reconnected) => {
        c().wsOpened();
        if (reconnected) qc.invalidateQueries(); // missed messages while disconnected
      },
      onHello: (h) => c().setHello(h),
      onMessage: (msg) => {
        c().markSeen();
        applyWsMessage(qc, homeId, msg);
        // Legacy "verzonden" markers (usePending): done only when nothing is in flight any more.
        if (msg.type === 'zone.updated' && !msg.data.pending_command) ref.current.confirm(`zone:${msg.data.id}`);
        if (msg.type === 'lights.updated' && !['queued', 'sending'].includes(msg.data.last_command?.status ?? '')) ref.current.confirm(`group:${msg.data.id}`);
      },
      onFailure: () => c().wsFailed(),
      onReauth: (err) => {
        api.authStatus().then(
          (s) => {
            if (s.user) {
              // Session fine, only the live connection is refused (e.g. origin check behind a proxy).
              c().wsDenied(err?.reason ?? 'auth_required');
              live.retryIn(DENIED_RETRY_MS);
            } else {
              live.stop();
              c().wsStopped();
              toLogin(err?.code ?? 'auth_required', err?.reason);
              qc.setQueryData(qk.auth, s);
            }
          },
          () => {
            c().wsFailed();
            live.retryIn(backoffMs(2));
          },
        );
      },
      onRevoked: (err) => {
        c().wsStopped();
        toLogin(err?.code ?? 'token_revoked', err?.reason);
      },
    });
    c().wsConnecting();
    live.start();
    const nudge = () => {
      if (document.visibilityState !== 'hidden') live.nudge();
    };
    window.addEventListener('online', nudge);
    document.addEventListener('visibilitychange', nudge);
    return () => {
      window.removeEventListener('online', nudge);
      document.removeEventListener('visibilitychange', nudge);
      live.stop();
      c().wsStopped();
    };
  }, [homeId, qc]);
}
