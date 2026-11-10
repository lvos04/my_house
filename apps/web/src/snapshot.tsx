// "Laatst bekend" also after closing the tab: the last overview/zones/lights/energy data is kept in
// localStorage per home (with a timestamp, size-capped) and loaded at startup. Opened without a
// connection, the app shows that data dimmed with its time; all actions stay disabled while offline.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { QueryClient, QueryKey } from '@tanstack/react-query';
import { onConnectionEvent } from './api/client';
import { readStoredHome } from './state';

export const SNAPSHOT_PREFIX = 'shc.snap.v1:';
const GLOBAL_KEY = SNAPSHOT_PREFIX + 'global';
/** Per home; larger snapshots drop their least important entries first. */
export const SNAPSHOT_MAX_BYTES = 350_000;
const MAX_HOMES = 3;
const SAVE_DELAY_MS = 1500;

/** Query keys worth keeping, most important first. */
const HOME_KEYS = ['overview', 'zones', 'lights', 'energyLive', 'energy'] as const;
const GLOBAL_KEYS = ['auth', 'homes'] as const;

interface Storage {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
  readonly length: number;
  key(i: number): string | null;
}
export interface Snapshot {
  savedAt: number;
  entries: [QueryKey, unknown][];
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function pick(qc: QueryClient, first: string, homeId?: string): [QueryKey, unknown][] {
  return qc
    .getQueryCache()
    .findAll({ queryKey: homeId ? [first, homeId] : [first] })
    .filter((q) => q.state.status === 'success' && q.state.data !== undefined)
    .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)
    .slice(0, first === 'energy' ? 2 : 1)
    .map((q) => [q.queryKey, q.state.data]);
}

/** Serialize within the size cap, dropping the least important entries first. Null when even the
 * most important entry does not fit. */
export function encodeSnapshot(snap: Snapshot, maxBytes: number = SNAPSHOT_MAX_BYTES): string | null {
  const entries = [...snap.entries];
  while (entries.length) {
    const s = JSON.stringify({ savedAt: snap.savedAt, entries });
    if (s.length <= maxBytes) return s;
    entries.pop();
  }
  return null;
}

export function saveSnapshot(qc: QueryClient, homeId: string | null, st: Storage | null = storage(), now: number = Date.now()) {
  if (!st) return;
  try {
    const global = GLOBAL_KEYS.flatMap((k) => pick(qc, k));
    if (global.length) {
      const g = encodeSnapshot({ savedAt: now, entries: global });
      if (g) st.setItem(GLOBAL_KEY, g);
    }
    if (!homeId) return;
    const entries = HOME_KEYS.flatMap((k) => pick(qc, k, homeId));
    if (!entries.length) return;
    const s = encodeSnapshot({ savedAt: now, entries });
    if (!s) return;
    st.setItem(SNAPSHOT_PREFIX + homeId, s);
    pruneHomes(st);
  } catch {
    /* quota exceeded or storage blocked: the app works without it */
  }
}

function pruneHomes(st: Storage) {
  const homes: { key: string; savedAt: number }[] = [];
  for (let i = 0; i < st.length; i++) {
    const k = st.key(i);
    if (!k || !k.startsWith(SNAPSHOT_PREFIX) || k === GLOBAL_KEY) continue;
    homes.push({ key: k, savedAt: loadRaw(st, k)?.savedAt ?? 0 });
  }
  homes.sort((a, b) => b.savedAt - a.savedAt);
  for (const h of homes.slice(MAX_HOMES)) st.removeItem(h.key);
}

function loadRaw(st: Storage, key: string): Snapshot | null {
  try {
    const raw = st.getItem(key);
    if (!raw) return null;
    const s = JSON.parse(raw) as Snapshot;
    return typeof s?.savedAt === 'number' && Array.isArray(s.entries) ? s : null;
  } catch {
    return null;
  }
}

export function loadSnapshot(homeId: string | null, st: Storage | null = storage()): { global: Snapshot | null; home: Snapshot | null } {
  if (!st) return { global: null, home: null };
  return { global: loadRaw(st, GLOBAL_KEY), home: homeId ? loadRaw(st, SNAPSHOT_PREFIX + homeId) : null };
}

/** Put the stored data into the query cache, marked with its original time and as stale, so the first
 * reader refetches it at once — also when the snapshot is younger than the client's staleTime (a
 * reload within seconds would otherwise keep, say, an empty list of homes as if it were fresh).
 * Returns when the home data was saved, or null. Never overwrites fresher data. */
export function hydrateFromSnapshot(qc: QueryClient, homeId: string | null, st: Storage | null = storage()): number | null {
  const { global, home } = loadSnapshot(homeId, st);
  for (const snap of [global, home]) {
    if (!snap) continue;
    for (const [key, data] of snap.entries) {
      const existing = qc.getQueryState(key);
      if (existing?.data !== undefined && existing.dataUpdatedAt >= snap.savedAt) continue;
      qc.setQueryData(key, data, { updatedAt: snap.savedAt });
      // Last known data is never fresh: mark it invalidated without fetching now (no observer yet).
      void qc.invalidateQueries({ queryKey: key, exact: true, refetchType: 'none' });
    }
  }
  return home?.savedAt ?? null;
}

export function clearSnapshots(st: Storage | null = storage()) {
  if (!st) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < st.length; i++) {
      const k = st.key(i);
      if (k?.startsWith(SNAPSHOT_PREFIX)) keys.push(k);
    }
    keys.forEach((k) => st.removeItem(k));
  } catch {
    /* ignore */
  }
}

/** Keep saving while the app runs (debounced on every successful fetch or live update). */
export function persistSnapshots(qc: QueryClient, getHomeId: () => string | null = readStoredHome): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const unsub = qc.getQueryCache().subscribe((ev) => {
    if (ev.type !== 'updated' || ev.action.type !== 'success') return;
    const first = ev.query.queryKey[0];
    if (!(HOME_KEYS as readonly unknown[]).includes(first) && !(GLOBAL_KEYS as readonly unknown[]).includes(first)) return;
    clearTimeout(timer);
    timer = setTimeout(() => saveSnapshot(qc, getHomeId()), SAVE_DELAY_MS);
  });
  return () => {
    clearTimeout(timer);
    unsub();
  };
}

/* ---------- React ---------- */

export interface SnapshotInfo {
  /** When the shown data was saved (ms), while nothing fresh has arrived yet; else null. */
  savedAt: number | null;
}
const SnapshotContext = createContext<SnapshotInfo>({ savedAt: null });
export const useSnapshotInfo = () => useContext(SnapshotContext);

/** Loads the snapshot before the first render and keeps it up to date. */
export function SnapshotProvider({ client, children }: { client: QueryClient; children: ReactNode }) {
  const [savedAt, setSavedAt] = useState<number | null>(() => hydrateFromSnapshot(client, readStoredHome()));
  useEffect(() => persistSnapshots(client), [client]);
  useEffect(() => {
    if (savedAt === null) return;
    // The first real answer from the API means the data is (being) refreshed.
    return onConnectionEvent((ev) => {
      if (ev.type === 'ok') setSavedAt(null);
    });
  }, [savedAt]);
  return <SnapshotContext.Provider value={{ savedAt }}>{children}</SnapshotContext.Provider>;
}
