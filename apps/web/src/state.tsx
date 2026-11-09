import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { onConnectionEvent } from './api/client';
import type { HelloData } from './api/types';

/* ---------- Connection (offline rule) ---------- */

/** "denied": the server refused the live connection (close 4401) while the web session itself is fine
 * (e.g. an origin mismatch behind a proxy). That is not "offline". */
export type WsState = 'idle' | 'connecting' | 'open' | 'closed' | 'denied';

/** Show "offline" only after this many consecutive failures… */
export const OFFLINE_AFTER_FAILURES = 3;
/** …or when the first failure is this long ago without any success (verbeterlijst 4). */
export const OFFLINE_AFTER_MS = 15_000;

export interface FailureState {
  failures: number;
  since: number | null;
}
export const NO_FAILURES: FailureState = { failures: 0, since: null };

export function addFailure(s: FailureState, now: number): FailureState {
  return { failures: s.failures + 1, since: s.since ?? now };
}
/** The offline rule: 3 consecutive failures or 15 s since the first one. */
export function isDown(s: FailureState, now: number): boolean {
  return s.failures >= OFFLINE_AFTER_FAILURES || (s.since !== null && now - s.since >= OFFLINE_AFTER_MS);
}
function msUntilDown(s: FailureState, now: number): number | null {
  if (s.since === null || isDown(s, now)) return null;
  return Math.max(0, s.since + OFFLINE_AFTER_MS - now);
}

/** Why the user has to log in again (shown on the login page). */
export interface AuthNotice {
  code: string; // auth_required | token_revoked | token_expired
  reason?: string; // logout | password_changed | password_reset | user_disabled | user_deleted | expired | ...
}

export interface ConnectionValue {
  /** false once API requests fail per the offline rule. */
  apiReachable: boolean;
  wsState: WsState;
  /** Show the offline banner and disable all actions. */
  offline: boolean;
  /** nginx says the API is starting/updating (503 api_unavailable): Dutch message, else null. */
  starting: string | null;
  /** Cloudflare Access needs a new login and the automatic reload did not help: link to open. */
  accessLoginUrl: string | null;
  /** Live updates refused although logged in (reason from the server), else null. */
  liveDenied: string | null;
  /** WS hello payload (can_control, client_role) of the current connection. */
  hello: HelloData | null;
  authNotice: AuthNotice | null;
  lastSeen: Date | null;
  /** @deprecated ws.ts reports through wsConnecting/wsOpened/wsFailed/wsDenied. */
  setWsState: (s: WsState) => void;
  wsConnecting: () => void;
  wsOpened: () => void;
  wsFailed: () => void;
  wsDenied: (reason: string) => void;
  wsStopped: () => void;
  setHello: (h: HelloData | null) => void;
  setAuthNotice: (n: AuthNotice | null) => void;
  markSeen: () => void;
}

const ConnectionContext = createContext<ConnectionValue | null>(null);

export interface ConnectionInitial {
  apiReachable?: boolean;
  wsState?: WsState;
  lastSeen?: Date | null;
  starting?: string | null;
}

export function ConnectionProvider({ children, initial }: { children: ReactNode; initial?: ConnectionInitial }) {
  // `initial` puts the provider straight into a state (tests, previews): unreachable = already offline.
  const [api, setApi] = useState<FailureState>(initial?.apiReachable === false ? { failures: OFFLINE_AFTER_FAILURES, since: 0 } : NO_FAILURES);
  const [ws, setWs] = useState<FailureState>(initial?.wsState === 'closed' ? { failures: OFFLINE_AFTER_FAILURES, since: 0 } : NO_FAILURES);
  const [wsState, setWsStateRaw] = useState<WsState>(initial?.wsState ?? 'idle');
  const [lastSeen, setLastSeen] = useState<Date | null>(initial?.lastSeen ?? null);
  const [starting, setStarting] = useState<string | null>(initial?.starting ?? null);
  const [accessLoginUrl, setAccessLoginUrl] = useState<string | null>(null);
  const [liveDenied, setLiveDenied] = useState<string | null>(null);
  const [hello, setHello] = useState<HelloData | null>(null);
  const [authNotice, setAuthNotice] = useState<AuthNotice | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const markSeen = useCallback(() => setLastSeen(new Date()), []);

  useEffect(
    () =>
      onConnectionEvent((ev) => {
        const t = Date.now();
        setNow(t);
        if (ev.type === 'ok') {
          setApi(NO_FAILURES);
          setStarting(null);
          setAccessLoginUrl(null);
          setLastSeen(new Date());
        } else if (ev.type === 'unreachable') {
          setApi((s) => addFailure(s, t));
        } else if (ev.type === 'starting') {
          setStarting(ev.message);
        } else if (ev.type === 'access_login') {
          setAccessLoginUrl(ev.url);
        } else if (ev.type === 'auth_required') {
          setAuthNotice((n) => n ?? { code: ev.code, reason: 'expired' });
        }
      }),
    [],
  );

  // Re-evaluate when the 15 s mark passes without new events.
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => {
    const t = Date.now();
    const waits = [msUntilDown(api, t), wsState === 'open' || wsState === 'denied' ? null : msUntilDown(ws, t)].filter((x): x is number => x !== null);
    if (!waits.length) return;
    timer.current = setTimeout(() => setNow(Date.now()), Math.min(...waits) + 20);
    return () => clearTimeout(timer.current);
  }, [api, ws, wsState, now]);

  const wsConnecting = useCallback(() => setWsStateRaw('connecting'), []);
  const wsOpened = useCallback(() => {
    setWs(NO_FAILURES);
    setWsStateRaw('open');
    setLiveDenied(null);
    setLastSeen(new Date());
  }, []);
  const wsFailed = useCallback(() => {
    const t = Date.now();
    setNow(t);
    setWs((s) => addFailure(s, t));
    setWsStateRaw('closed');
  }, []);
  const wsDenied = useCallback((reason: string) => {
    setWs(NO_FAILURES);
    setWsStateRaw('denied');
    setLiveDenied(reason);
  }, []);
  const wsStopped = useCallback(() => {
    setWs(NO_FAILURES);
    setWsStateRaw('idle');
    setHello(null);
  }, []);
  const setWsState = useCallback(
    (s: WsState) => {
      if (s === 'open') wsOpened();
      else if (s === 'closed') wsFailed();
      else if (s === 'idle') wsStopped();
      else setWsStateRaw(s);
    },
    [wsOpened, wsFailed, wsStopped],
  );

  const value = useMemo<ConnectionValue>(() => {
    const t = Math.max(now, Date.now());
    const apiDown = isDown(api, t);
    const wsDown = wsState === 'closed' && isDown(ws, t);
    return {
      apiReachable: !apiDown,
      wsState,
      offline: apiDown || wsDown,
      starting,
      accessLoginUrl,
      liveDenied,
      hello,
      authNotice,
      lastSeen,
      setWsState,
      wsConnecting,
      wsOpened,
      wsFailed,
      wsDenied,
      wsStopped,
      setHello,
      setAuthNotice,
      markSeen,
    };
  }, [api, ws, wsState, now, starting, accessLoginUrl, liveDenied, hello, authNotice, lastSeen, setWsState, wsConnecting, wsOpened, wsFailed, wsDenied, wsStopped, markSeen]);
  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

export function useConnection(): ConnectionValue {
  const v = useContext(ConnectionContext);
  if (!v) throw new Error('ConnectionProvider ontbreekt');
  return v;
}
/** Same as useConnection() but null outside a ConnectionProvider (for optional use). */
export function useConnectionOptional(): ConnectionValue | null {
  return useContext(ConnectionContext);
}

/** True when actions may be sent. Never allow actions while offline or while the API is starting. */
export function useCanAct(): boolean {
  const c = useConnection();
  return !c.offline && !c.starting;
}

/* ---------- Pending commands ("verzonden" until confirmed) ---------- */
// Kept for existing pages. New code: use trackCommand()/useCommand() from ./commands, which follow the
// real command.updated outcome.

export interface Pending {
  sentAt: number;
  label: string;
  timedOut: boolean;
}
interface PendingValue {
  pending: Record<string, Pending>;
  markSent: (key: string, label: string) => void;
  confirm: (key: string) => void;
}
const PendingContext = createContext<PendingValue | null>(null);

const CONFIRM_TIMEOUT_MS = 30_000;

export function PendingProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const confirm = useCallback((key: string) => {
    clearTimeout(timers.current[key]);
    delete timers.current[key];
    setPending((p) => {
      if (!(key in p)) return p;
      const next = { ...p };
      delete next[key];
      return next;
    });
  }, []);

  const markSent = useCallback((key: string, label: string) => {
    clearTimeout(timers.current[key]);
    setPending((p) => ({ ...p, [key]: { sentAt: Date.now(), label, timedOut: false } }));
    timers.current[key] = setTimeout(() => {
      setPending((p) => (p[key] ? { ...p, [key]: { ...p[key], timedOut: true } } : p));
    }, CONFIRM_TIMEOUT_MS);
  }, []);

  useEffect(() => {
    const t = timers.current;
    return () => Object.values(t).forEach(clearTimeout);
  }, []);

  const value = useMemo(() => ({ pending, markSent, confirm }), [pending, markSent, confirm]);
  return <PendingContext.Provider value={value}>{children}</PendingContext.Provider>;
}

export function usePending(): PendingValue {
  const v = useContext(PendingContext);
  if (!v) throw new Error('PendingProvider ontbreekt');
  return v;
}

/* ---------- Selected home ---------- */

export const HOME_KEY = 'shc.home';

interface HomeValue {
  homeId: string | null;
  setHomeId: (id: string) => void;
}
const HomeContext = createContext<HomeValue>({ homeId: null, setHomeId: () => {} });

export function readStoredHome(): string | null {
  try {
    return localStorage.getItem(HOME_KEY);
  } catch {
    return null;
  }
}

export function HomeProvider({ children }: { children: ReactNode }) {
  const [homeId, setHomeIdState] = useState<string | null>(readStoredHome);
  const setHomeId = useCallback((id: string) => {
    setHomeIdState(id);
    try {
      localStorage.setItem(HOME_KEY, id);
    } catch {
      /* storage may be blocked */
    }
  }, []);
  const value = useMemo(() => ({ homeId, setHomeId }), [homeId, setHomeId]);
  return <HomeContext.Provider value={value}>{children}</HomeContext.Provider>;
}
export const useHome = () => useContext(HomeContext);
