import { useEffect, useRef } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { API_BASE } from './api/client';
import type { Alert, LightGroup, LivePower, Overview, StatusCheck, WsMessage, Zone } from './api/types';
import { useConnection, usePending } from './state';

export function wsUrl(homeId: string, loc: Location = window.location): string {
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${loc.host}${API_BASE}/ws?home_id=${encodeURIComponent(homeId)}`;
}

/** Exponential backoff with jitter, capped at 30 s. */
export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(30_000, 1000 * 2 ** attempt);
  return Math.round(base / 2 + rand() * (base / 2));
}

function patchOverview(qc: QueryClient, homeId: string, fn: (o: Overview) => Overview) {
  qc.setQueryData<Overview>(['overview', homeId], (o) => (o ? fn(o) : o));
}

export function applyWsMessage(qc: QueryClient, homeId: string, msg: WsMessage) {
  switch (msg.type) {
    case 'zone.updated': {
      const z: Zone = msg.data;
      qc.setQueryData(['zone', z.id], z);
      qc.setQueryData<Zone[]>(['zones', homeId], (zs) => zs?.map((x) => (x.id === z.id ? z : x)));
      patchOverview(qc, homeId, (o) => ({ ...o, zones: o.zones.map((x) => (x.id === z.id ? z : x)) }));
      break;
    }
    case 'lights.updated': {
      const g: LightGroup = msg.data;
      qc.setQueryData<{ groups: LightGroup[] } | undefined>(['lights', homeId], (l) =>
        l ? { ...l, groups: l.groups.map((x) => (x.id === g.id ? g : x)) } : l,
      );
      patchOverview(qc, homeId, (o) => ({ ...o, lights: o.lights.map((x) => (x.id === g.id ? g : x)) }));
      break;
    }
    case 'energy.live': {
      const p: LivePower = msg.data;
      qc.setQueryData(['energyLive', homeId], p);
      patchOverview(qc, homeId, (o) => ({ ...o, power_now: p }));
      break;
    }
    case 'alert.created': {
      const a: Alert = msg.data;
      patchOverview(qc, homeId, (o) => ({ ...o, alerts: [a, ...o.alerts.filter((x) => x.id !== a.id)] }));
      qc.invalidateQueries({ queryKey: ['alerts', homeId] });
      break;
    }
    case 'status.updated': {
      const s: StatusCheck[] = msg.data;
      patchOverview(qc, homeId, (o) => ({ ...o, status: s }));
      break;
    }
  }
}

/** Live updates for the selected home. Reconnects with backoff and refetches everything after a reconnect. */
export function useLiveUpdates(homeId: string | null) {
  const qc = useQueryClient();
  const { setWsState, markSeen } = useConnection();
  const { confirm } = usePending();
  const handlers = useRef({ confirm, markSeen });
  handlers.current = { confirm, markSeen };

  useEffect(() => {
    if (!homeId || typeof WebSocket === 'undefined') return;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let everOpened = false;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      setWsState('connecting');
      ws = new WebSocket(wsUrl(homeId));
      ws.onopen = () => {
        if (everOpened) {
          // Missed messages while disconnected: fetch full state again.
          qc.invalidateQueries();
        }
        everOpened = true;
        attempt = 0;
        setWsState('open');
        handlers.current.markSeen();
      };
      ws.onmessage = (ev) => {
        let msg: WsMessage;
        try {
          msg = JSON.parse(ev.data as string) as WsMessage;
        } catch {
          return;
        }
        handlers.current.markSeen();
        applyWsMessage(qc, homeId, msg);
        if (msg.type === 'zone.updated') handlers.current.confirm(`zone:${msg.data.id}`);
        if (msg.type === 'lights.updated') handlers.current.confirm(`group:${msg.data.id}`);
      };
      ws.onclose = () => {
        if (stopped) return;
        setWsState('closed');
        timer = setTimeout(connect, backoffMs(attempt++));
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
      setWsState('idle');
    };
  }, [homeId, qc, setWsState]);
}
