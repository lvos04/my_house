import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { applyWsMessage, backoffMs, wsUrl } from '../ws';
import { todaySegments } from '../pages/Climate';
import { overview, zone } from './fixtures';
import type { Overview } from '../api/types';

describe('websocket helpers', () => {
  it('builds ws urls and caps backoff', () => {
    expect(wsUrl('h1', { protocol: 'https:', host: 'huis.nl' } as Location)).toBe('wss://huis.nl/api/v1/ws?home_id=h1');
    expect(backoffMs(0, () => 1)).toBe(1000);
    expect(backoffMs(20, () => 1)).toBe(30000);
    expect(backoffMs(20, () => 0)).toBe(15000);
  });

  it('applies zone.updated to the cached overview', () => {
    const qc = new QueryClient();
    qc.setQueryData(['overview', 'h1'], overview);
    applyWsMessage(qc, 'h1', { type: 'zone.updated', data: zone({ target_c: 22 }) });
    expect(qc.getQueryData<Overview>(['overview', 'h1'])!.zones[0].target_c).toBe(22);
  });
});

describe('schedule segments', () => {
  it('carries the previous day block into the morning', () => {
    const segs = todaySegments({ blocks: [
      { days: [0, 1, 2, 3, 4, 5, 6], start: '07:00', target_c: 21, label: 'Comfort' },
      { days: [0, 1, 2, 3, 4, 5, 6], start: '22:30', target_c: 18.5, label: 'Nacht' },
    ] }, 2);
    expect(segs).toEqual([
      { from: 0, to: 420, target_c: 18.5, label: 'Nacht' },
      { from: 420, to: 1350, target_c: 21, label: 'Comfort' },
      { from: 1350, to: 1440, target_c: 18.5, label: 'Nacht' },
    ]);
  });
});
