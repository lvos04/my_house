import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { qk } from '../api/keys';
import type { Alert, Lights, Overview } from '../api/types';
import {
  applyCommandSnapshot,
  applyCommandUpdate,
  commandPhase,
  commandText,
  getCommand,
  latestCommandId,
  QUEUED_TIMEOUT_MS,
  resetCommands,
  SENT_TIMEOUT_MS,
  trackCommand,
  useCommand,
} from '../commands';
import { Debouncer, SETPOINT_DEBOUNCE_MS, stepSetpoint, useDebouncedSetpoint } from '../setpoint';
import { clearSnapshots, encodeSnapshot, hydrateFromSnapshot, loadSnapshot, saveSnapshot, SNAPSHOT_PREFIX } from '../snapshot';
import { applyWsMessage } from '../ws';
import { BUTTON_EVENT_LABEL, t, KIND_LABEL, PROVIDER_LABEL } from '../i18n';
import { group, overview, zone } from './fixtures';

beforeEach(() => resetCommands());
afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe('command store (item 23)', () => {
  it('tracks a command until command.updated says confirmed; final statuses are never overwritten', () => {
    trackCommand('c1', { key: 'zone:z1', label: 'Doeltemperatuur 21°' }, 1000);
    expect(getCommand('c1')!.status).toBe('queued');
    expect(latestCommandId('zone:z1')).toBe('c1');
    applyCommandUpdate({ id: 'c1', status: 'sent', detail: 'nog niet bevestigd', attempts: 1 }, 2000);
    expect(commandPhase(getCommand('c1')!, 2000)).toBe('sent');
    applyCommandUpdate({ id: 'c1', status: 'confirmed', detail: null }, 3000);
    applyCommandUpdate({ id: 'c1', status: 'queued' }, 4000); // late duplicate
    expect(getCommand('c1')!.status).toBe('confirmed');
    expect(commandText(getCommand('c1')!)).toBe('Bevestigd');
  });

  it('keeps an update that arrives before the 202 answer, and shows retries and failures with the reason', () => {
    applyCommandUpdate({ id: 'c2', status: 'queued', detail: 'Geen antwoord — opnieuw proberen (poging 2 van 3)', attempts: 1, retrying: true }, 1000);
    trackCommand('c2', { key: 'zone:z1' }, 1100);
    const c = getCommand('c2')!;
    expect(c.retrying).toBe(true);
    expect(commandText(c, 1200)).toBe('Mislukt, wordt opnieuw geprobeerd…: Geen antwoord — opnieuw proberen (poging 2 van 3)');
    applyCommandUpdate({ id: 'c2', status: 'failed', detail: 'Airco reageert niet' }, 2000);
    expect(commandText(getCommand('c2')!)).toBe('Mislukt: Airco reageert niet');
    applyCommandUpdate({ id: 'c3', status: 'denied', detail: 'Veilige modus' });
    expect(commandText(getCommand('c3')!)).toBe('Geweigerd: Veilige modus');
  });

  it('times out locally: queued after 30 s, sent after 150 s', () => {
    trackCommand('c4', {}, 0);
    expect(commandPhase(getCommand('c4')!, QUEUED_TIMEOUT_MS - 1)).toBe('sending');
    expect(commandPhase(getCommand('c4')!, QUEUED_TIMEOUT_MS + 1)).toBe('timeout');
    applyCommandUpdate({ id: 'c4', status: 'sent' }, 100);
    expect(commandPhase(getCommand('c4')!, 100 + SENT_TIMEOUT_MS - 1)).toBe('sent');
    expect(commandPhase(getCommand('c4')!, 100 + SENT_TIMEOUT_MS + 1)).toBe('timeout');
  });

  it('takes statuses from zone.pending_command / group.last_command snapshots', () => {
    applyCommandSnapshot('group:g1', { id: 'c5', action: 'set_group', status: 'failed', detail: 'Hue Bridge meldt: fout', at: null });
    expect(latestCommandId('group:g1')).toBe('c5');
    expect(getCommand('c5')!.detail).toBe('Hue Bridge meldt: fout');
  });

  it('useCommand re-renders on updates', () => {
    function View() {
      const c = useCommand('c6');
      return <span data-testid="c">{c ? `${c.phase}|${c.text}` : 'none'}</span>;
    }
    render(<View />);
    expect(screen.getByTestId('c')).toHaveTextContent('none');
    act(() => void trackCommand('c6'));
    expect(screen.getByTestId('c')).toHaveTextContent('sending|Wordt verzonden…');
    act(() => applyCommandUpdate({ id: 'c6', status: 'confirmed' }));
    expect(screen.getByTestId('c')).toHaveTextContent('confirmed|Bevestigd');
  });
});

describe('debounced setpoint (item 23)', () => {
  it('Debouncer sends only the last value once after the quiet period', () => {
    vi.useFakeTimers();
    const send = vi.fn();
    const d = new Debouncer<number>(send, SETPOINT_DEBOUNCE_MS);
    d.push(21.5);
    vi.advanceTimersByTime(500);
    d.push(22);
    vi.advanceTimersByTime(500);
    d.push(22.5);
    expect(send).not.toHaveBeenCalled();
    vi.advanceTimersByTime(SETPOINT_DEBOUNCE_MS);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(22.5);
    d.flush();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('steps snap to the unit step and stay in range', () => {
    expect(stepSetpoint(21, 1, 0.5, 16, 30)).toBe(21.5);
    expect(stepSetpoint(20.7, 1, 0.5, 16, 30)).toBe(21);
    expect(stepSetpoint(20.7, -1, 0.5, 16, 30)).toBe(20.5);
    expect(stepSetpoint(30, 1, 1, 16, 30)).toBe(30);
    expect(stepSetpoint(16, -1, 1, 16, 30)).toBe(16);
    expect(stepSetpoint(0.1 + 0.2, 1, 0.1, 0, 1)).toBe(0.4);
  });

  it('the hook shows the draft at once, sends one final value and follows the command', async () => {
    vi.useFakeTimers();
    const onSend = vi.fn(async () => 'cmd-9');
    let api: ReturnType<typeof useDebouncedSetpoint> | null = null;
    function Ctl({ value }: { value: number }) {
      api = useDebouncedSetpoint({ value, min: 16, max: 30, step: 0.5, onSend });
      return <span data-testid="v">{api.value}|{api.waiting ? 'wait' : 'idle'}|{api.command?.phase ?? '-'}</span>;
    }
    const { rerender } = render(<Ctl value={21} />);
    act(() => api!.bump(1));
    act(() => api!.bump(1));
    act(() => api!.bump(1));
    expect(screen.getByTestId('v')).toHaveTextContent('22.5|wait|-');
    await act(async () => {
      vi.advanceTimersByTime(SETPOINT_DEBOUNCE_MS + 10);
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith(22.5);
    act(() => void trackCommand('cmd-9'));
    expect(screen.getByTestId('v')).toHaveTextContent('22.5|idle|sending');
    act(() => applyCommandUpdate({ id: 'cmd-9', status: 'confirmed' }));
    rerender(<Ctl value={22.5} />);
    expect(screen.getByTestId('v')).toHaveTextContent('22.5|idle|confirmed');
  });

  it('reverts the draft when the command fails', async () => {
    vi.useFakeTimers();
    let api: ReturnType<typeof useDebouncedSetpoint> | null = null;
    function Ctl() {
      api = useDebouncedSetpoint({ value: 21, min: 16, max: 30, step: 1, onSend: async () => 'cmd-10' });
      return <span data-testid="v">{api.value}</span>;
    }
    render(<Ctl />);
    act(() => api!.bump(1));
    expect(screen.getByTestId('v')).toHaveTextContent('22');
    await act(async () => {
      vi.advanceTimersByTime(SETPOINT_DEBOUNCE_MS + 10);
    });
    act(() => void trackCommand('cmd-10'));
    act(() => applyCommandUpdate({ id: 'cmd-10', status: 'failed', detail: 'Kies eerst een modus' }));
    expect(screen.getByTestId('v')).toHaveTextContent('21');
  });
});

describe('offline snapshot ("laatst bekend")', () => {
  it('saves per home with a timestamp and loads it into a fresh client, marked old', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.overview('h1'), overview);
    qc.setQueryData(qk.zones('h1'), [zone()]);
    qc.setQueryData(qk.auth, { setup_required: false, user: { id: 'u1', username: 'a', role: 'admin' } });
    qc.setQueryData(qk.homes, [overview.home]);
    saveSnapshot(qc, 'h1', localStorage, 1_700_000_000_000);
    expect(localStorage.getItem(SNAPSHOT_PREFIX + 'h1')).toContain('"savedAt":1700000000000');
    const fresh = new QueryClient();
    expect(hydrateFromSnapshot(fresh, 'h1', localStorage)).toBe(1_700_000_000_000);
    expect(fresh.getQueryData<Overview>(qk.overview('h1'))!.home.name).toBe('Thuis');
    expect(fresh.getQueryState(qk.overview('h1'))!.dataUpdatedAt).toBe(1_700_000_000_000);
    expect(fresh.getQueryData(qk.auth)).toMatchObject({ user: { username: 'a' } });
    expect(hydrateFromSnapshot(new QueryClient(), 'other', localStorage)).toBeNull();
    clearSnapshots(localStorage);
    expect(loadSnapshot('h1', localStorage).home).toBeNull();
  });

  it('drops the least important entries when over the size cap, and survives blocked storage', () => {
    const big = { savedAt: 1, entries: [[['overview', 'h1'], { a: 'x'.repeat(100) }], [['energy', 'h1', 'day', 'd'], { b: 'y'.repeat(500) }]] as [unknown[], unknown][] };
    const s = encodeSnapshot(big, 300)!;
    expect(JSON.parse(s).entries).toHaveLength(1);
    expect(encodeSnapshot(big, 10)).toBeNull();
    const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('quota'); }, removeItem: () => {}, length: 0, key: () => null };
    const qc = new QueryClient();
    qc.setQueryData(qk.overview('h1'), overview);
    expect(() => saveSnapshot(qc, 'h1', broken)).not.toThrow();
    expect(hydrateFromSnapshot(qc, 'h1', broken)).toBeNull();
  });

  // Gevonden in CI-run 42785063693 (browsertest "eerste start"): een snapshot van een paar seconden oud (woningen nog
  // leeg) werd na het herladen als vers gezien (staleTime 10 s) en niet opnieuw opgehaald, zodat de app de net
  // gemaakte woning niet kende. Laatst bekende gegevens zijn nooit vers: de eerste lezer haalt ze meteen opnieuw op.
  it('refetches snapshot data at once, also when the snapshot is only seconds old', async () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.homes, []);
    saveSnapshot(qc, null, localStorage, Date.now() - 1_000);
    const fresh = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: false } } });
    hydrateFromSnapshot(fresh, null, localStorage);
    expect(fresh.getQueryData(qk.homes)).toEqual([]);
    const queryFn = vi.fn(async () => [overview.home]);
    const unsubscribe = new QueryObserver(fresh, { queryKey: qk.homes, queryFn }).subscribe(() => {});
    await vi.waitFor(() => expect(fresh.getQueryData(qk.homes)).toEqual([overview.home]));
    expect(queryFn).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('never overwrites fresher data and does not mark it stale', async () => {
    const old = new QueryClient();
    old.setQueryData(qk.homes, []);
    saveSnapshot(old, null, localStorage, Date.now() - 60_000);
    const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: false } } });
    qc.setQueryData(qk.homes, [overview.home]);
    hydrateFromSnapshot(qc, null, localStorage);
    const queryFn = vi.fn(async () => [overview.home]);
    const unsubscribe = new QueryObserver(qc, { queryKey: qk.homes, queryFn }).subscribe(() => {});
    await act(async () => {});
    expect(qc.getQueryData(qk.homes)).toEqual([overview.home]);
    expect(queryFn).not.toHaveBeenCalled();
    unsubscribe();
  });
});

describe('live messages', () => {
  it('alert.resolved removes the alert, status.updated replaces the status, command.updated feeds the store', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.overview('h1'), overview);
    qc.setQueryData<Alert[]>(qk.alerts('h1'), overview.alerts);
    applyWsMessage(qc, 'h1', { type: 'alert.resolved', data: { id: 'a1' } });
    expect(qc.getQueryData<Overview>(qk.overview('h1'))!.alerts).toEqual([]);
    expect(qc.getQueryData<Alert[]>(qk.alerts('h1'))).toEqual([]);
    applyWsMessage(qc, 'h1', { type: 'status.updated', data: [{ key: 'worker', label: 'Achtergronddienst', state: 'error', detail: 'Geen hartslag' }] });
    expect(qc.getQueryData<Overview>(qk.overview('h1'))!.status[0].key).toBe('worker');
    applyWsMessage(qc, 'h1', { type: 'command.updated', data: { id: 'c7', status: 'confirmed' } });
    expect(getCommand('c7')!.status).toBe('confirmed');
    applyWsMessage(qc, 'h1', { type: 'zone.updated', data: zone({ pending_command: { id: 'c8', status: 'queued', since: null } }) });
    expect(latestCommandId('zone:z1')).toBe('c8');
  });

  it('refresh invalidates all queries; lights.updated replaces the group', () => {
    const qc = new QueryClient();
    qc.setQueryData<Lights>(qk.lights('h1'), { groups: [group()], scenes: [], lights: [] });
    qc.setQueryData(qk.overview('h1'), overview);
    applyWsMessage(qc, 'h1', { type: 'lights.updated', data: group({ on: false }) });
    expect(qc.getQueryData<Lights>(qk.lights('h1'))!.groups[0].on).toBe(false);
    applyWsMessage(qc, 'h1', { type: 'refresh', data: { reason: 'zone.updated' } });
    expect(qc.getQueryState(qk.lights('h1'))!.isInvalidated).toBe(true);
    // unknown types (e.g. auth.revoked, never sent to clients) are ignored
    expect(() => applyWsMessage(qc, 'h1', { type: 'auth.revoked', data: {} } as never)).not.toThrow();
  });
});

describe('Dutch dictionary', () => {
  it('maps raw values and never returns the raw key with underscores', () => {
    expect(t(BUTTON_EVENT_LABEL, 'initial_press')).toBe('Indrukken');
    expect(t(BUTTON_EVENT_LABEL, 'long_release')).toBe('Loslaten na lang indrukken');
    expect(t(PROVIDER_LABEL, 'homewizard')).toBe('HomeWizard');
    expect(t(KIND_LABEL, 'energy_socket')).toBe('Energy Socket (stekker)');
    expect(t(KIND_LABEL, 'some_new_kind')).toBe('some new kind');
    expect(t(KIND_LABEL, null)).toBe('–');
  });
});
