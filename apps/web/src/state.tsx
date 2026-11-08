import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { onReachability } from './api/client';

/* ---------- Connection (offline rule) ---------- */

export type WsState = 'idle' | 'connecting' | 'open' | 'closed';

interface ConnectionValue {
  apiReachable: boolean;
  wsState: WsState;
  offline: boolean;
  lastSeen: Date | null;
  setWsState: (s: WsState) => void;
  markSeen: () => void;
}

const ConnectionContext = createContext<ConnectionValue | null>(null);

export function ConnectionProvider({ children, initial }: { children: ReactNode; initial?: Partial<Pick<ConnectionValue, 'apiReachable' | 'wsState' | 'lastSeen'>> }) {
  const [apiReachable, setApiReachable] = useState(initial?.apiReachable ?? true);
  const [wsState, setWsState] = useState<WsState>(initial?.wsState ?? 'idle');
  const [lastSeen, setLastSeen] = useState<Date | null>(initial?.lastSeen ?? null);

  const markSeen = useCallback(() => setLastSeen(new Date()), []);

  useEffect(
    () =>
      onReachability((ok) => {
        setApiReachable(ok);
        if (ok) setLastSeen(new Date());
      }),
    [],
  );

  const value = useMemo<ConnectionValue>(
    () => ({
      apiReachable,
      wsState,
      // "connecting" is a grace period; only a closed socket or a failed request counts as offline.
      offline: !apiReachable || wsState === 'closed',
      lastSeen,
      setWsState,
      markSeen,
    }),
    [apiReachable, wsState, lastSeen, markSeen],
  );
  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

export function useConnection(): ConnectionValue {
  const v = useContext(ConnectionContext);
  if (!v) throw new Error('ConnectionProvider ontbreekt');
  return v;
}

/** True when actions may be sent. Never allow actions while offline. */
export function useCanAct(): boolean {
  return !useConnection().offline;
}

/* ---------- Pending commands ("verzonden" until confirmed) ---------- */

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

interface HomeValue {
  homeId: string | null;
  setHomeId: (id: string) => void;
}
const HomeContext = createContext<HomeValue>({ homeId: null, setHomeId: () => {} });

export function HomeProvider({ children }: { children: ReactNode }) {
  const [homeId, setHomeIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem('shc.home');
    } catch {
      return null;
    }
  });
  const setHomeId = useCallback((id: string) => {
    setHomeIdState(id);
    try {
      localStorage.setItem('shc.home', id);
    } catch {
      /* storage may be blocked */
    }
  }, []);
  return <HomeContext.Provider value={{ homeId, setHomeId }}>{children}</HomeContext.Provider>;
}
export const useHome = () => useContext(HomeContext);
