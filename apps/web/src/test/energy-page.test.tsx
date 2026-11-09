import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EnergyPage } from '../pages/Energy';
import { applyWsMessage } from '../ws';
import { qk } from '../api/keys';
import type { EnergySummary, LivePower, Tariff } from '../api/types';
import { EMPTY_FORM, tariffErrors, tariffPayload } from '../energy/Tariffs';
import { canGoNext, periodLastDay, periodStart, periodTitle, shiftPeriod, todayIn } from '../energy/period';
import { exportProblem } from '../energy/Export';
import { bucketState } from '../energy/EnergyChart';
import { mockApi, newClient, Providers, viewer } from './helpers';
import { energy } from './fixtures';

const TZ = 'Pacific/Kiritimati'; // UTC+14: the home's day differs from the browser's
const NOW = new Date('2026-11-08T12:00:00Z'); // = Monday 9 Nov 02:00 in the home

const home = { id: 'h1', name: 'Thuis', timezone: TZ, currency: 'EUR', active: true };

function summary(over: Partial<EnergySummary> = {}): EnergySummary {
  return { ...energy, timezone: TZ, ...over };
}

const live = (w: number): LivePower => ({
  at: NOW.toISOString(),
  total_w: w,
  complete: false,
  note: 'Boilerstekker verouderd: overig is geschat.',
  parts: [
    { role: 'airco', label: 'Airco', watts: 300, quality: 'measured', at: NOW.toISOString(), stale: false },
    { role: 'boiler', label: 'Boiler', watts: null, quality: 'missing', at: null, stale: true },
  ],
});

function route(extra: (m: string, url: string, body: unknown) => ReturnType<Parameters<typeof mockApi>[0]> = () => undefined, tariffs: Tariff[] = []) {
  return (m: string, url: string, body: unknown) => {
    const r = extra(m, url, body);
    if (r) return r;
    if (url === '/api/v1/homes') return { body: [home] };
    if (url.includes('/energy/summary')) return { body: summary() };
    if (url.endsWith('/energy/live')) return { body: live(850) };
    if (url.endsWith('/homes/h1/tariffs')) return { body: tariffs };
    if (url.endsWith('/energy/limits')) return { body: { grid: 17.25, airco: 7.4, boiler: 3.7, gas: 10 } };
    return { body: [] };
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('tariff form model (item 7, 35)', () => {
  it('leaves out empty optional fields and accepts decimal commas', () => {
    expect(tariffPayload({ ...EMPTY_FORM, valid_from: '2026-01-01', import_per_kwh: '0,25' })).toEqual({ valid_from: '2026-01-01', import_per_kwh: 0.25 });
    expect(
      tariffPayload({ ...EMPTY_FORM, valid_from: '2026-01-01', import_per_kwh: '0.3', fixed_per_day: '1,05', export_per_kwh: '0,09', gas_per_m3: '1,45', dual: true, import_t1_per_kwh: '0,31', import_t2_per_kwh: '0,27' }),
    ).toEqual({ valid_from: '2026-01-01', import_per_kwh: 0.3, fixed_per_day: 1.05, export_per_kwh: 0.09, gas_per_m3: 1.45, import_t1_per_kwh: 0.31, import_t2_per_kwh: 0.27 });
    // T1/T2 typed but switched off: not sent.
    expect(tariffPayload({ ...EMPTY_FORM, valid_from: '2026-01-01', import_per_kwh: '0,3', import_t1_per_kwh: '0,31' })).toEqual({ valid_from: '2026-01-01', import_per_kwh: 0.3 });
  });

  it('explains what is wrong in Dutch', () => {
    expect(tariffErrors({ ...EMPTY_FORM, valid_from: '2026-01-01', import_per_kwh: '0,25' })).toEqual({});
    const e = tariffErrors({ ...EMPTY_FORM, valid_from: '', import_per_kwh: 'abc', fixed_per_day: '99', dual: true, import_t1_per_kwh: '0,3' });
    expect(e.valid_from).toMatch(/datum/);
    expect(e.import_per_kwh).toMatch(/getal/);
    expect(e.fixed_per_day).toMatch(/tussen 0 en 50/);
    expect(e.import_t2_per_kwh).toMatch(/T1 als T2/);
  });
});

describe('periods in the home time zone (item 39)', () => {
  it('computes today, period bounds and Dutch titles without the browser zone', () => {
    expect(todayIn(TZ, NOW)).toBe('2026-11-09');
    expect(todayIn('America/Los_Angeles', NOW)).toBe('2026-11-08');
    expect(periodStart('2026-11-09', 'week')).toBe('2026-11-09');
    expect(periodStart('2026-11-08', 'week')).toBe('2026-11-02');
    expect(periodLastDay('2026-11-08', 'week')).toBe('2026-11-08');
    expect(periodTitle('2026-11-04', 'week', '2026-11-09')).toBe('2–8 nov');
    expect(periodTitle('2026-10-28', 'week', '2026-11-09')).toBe('26 okt – 1 nov');
    expect(periodTitle('2025-11-04', 'week', '2026-11-09')).toBe('3–9 nov 2025');
    expect(periodTitle('2026-11-08', 'day', '2026-11-09')).toBe('zondag 8 november');
    expect(periodTitle('2026-02-10', 'month', '2026-11-09')).toBe('februari 2026');
    expect(shiftPeriod('2026-03-31', 'month', -1)).toBe('2026-02-01');
    expect(canGoNext('2026-11-09', 'day', '2026-11-09')).toBe(false);
    expect(canGoNext('2026-10-31', 'month', '2026-11-09')).toBe(true);
  });

  it('navigates previous/next and by date picker, asking the API for the home’s dates', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: NOW });
    const calls = mockApi(route());
    render(<Providers><EnergyPage /></Providers>);
    const user = userEvent.setup();
    expect(await screen.findByTestId('energy-title')).toHaveTextContent('november 2026');
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/energy/summary?period=month&date=2026-11-01'))).toBe(true));

    await user.click(screen.getByRole('tab', { name: 'Dag' }));
    expect(screen.getByTestId('energy-title')).toHaveTextContent('maandag 9 november');
    expect(screen.getByText('Energie · Vandaag')).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('period=day&date=2026-11-09'))).toBe(true));
    expect(screen.getByRole('button', { name: /^Volgende dag/ })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /^Vorige dag/ }));
    expect(screen.getByTestId('energy-title')).toHaveTextContent('zondag 8 november');
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('period=day&date=2026-11-08'))).toBe(true));
    expect(screen.getByRole('button', { name: /^Volgende dag/ })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Vandaag' }));
    expect(screen.getByTestId('energy-title')).toHaveTextContent('maandag 9 november');

    await user.click(screen.getByRole('tab', { name: 'Week' }));
    expect(screen.getByTestId('energy-title')).toHaveTextContent('9–15 nov');
    await user.click(screen.getByRole('button', { name: /^Vorige week/ }));
    expect(screen.getByTestId('energy-title')).toHaveTextContent('2–8 nov');
    expect(screen.getByText('Energie · Vorige week')).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('period=week&date=2026-11-02'))).toBe(true));

    // Date picker: a day in an earlier month.
    const picker = screen.getByLabelText('Datum') as HTMLInputElement;
    expect(picker.max).toBe('2026-11-09');
    fireEvent.change(picker, { target: { value: '2026-10-14' } });
    expect(screen.getByTestId('energy-title')).toHaveTextContent('12–18 okt');
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('period=week&date=2026-10-12'))).toBe(true));
  });

  it('is a real tab set: the selected period tab controls the panel with that period’s figures', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: NOW });
    mockApi(route());
    render(<Providers><EnergyPage /></Providers>);
    const user = userEvent.setup();
    const month = await screen.findByRole('tab', { name: 'Maand' });
    const panel = await screen.findByRole('tabpanel', { name: 'Maand' });
    expect(month).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAttribute('tabindex', '0');
    expect(await within(panel).findByRole('heading', { name: 'Verbruik per dag' })).toBeInTheDocument();
    // live power is not per period: outside the panel
    expect(await screen.findByTestId('live-power')).toBeInTheDocument();
    expect(within(panel).queryByTestId('live-power')).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Dag' }));
    const dayPanel = screen.getByRole('tabpanel', { name: 'Dag' });
    expect(screen.getByRole('tab', { name: 'Dag' })).toHaveAttribute('aria-controls', dayPanel.id);
    expect(screen.getByRole('tab', { name: 'Maand' })).not.toHaveAttribute('aria-controls');
    expect(await within(dayPanel).findByRole('heading', { name: 'Verbruik per uur' })).toBeInTheDocument();
  });
});

describe('energy page', () => {
  it('shows quality and notes per meter and bucket, gas, coverage, and hatched "Overig"', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: NOW });
    const s = summary({
      period: 'day',
      cost: 3.1,
      cost_coverage: 0.8,
      fixed_cost: 0.9,
      cost_note: 'Kosten gedeeltelijk: 80% van het verbruik valt onder een tarief.',
      meters: [
        { ...energy.meters[0], cost: 2.4, cost_coverage: 0.8, quality: 'estimated', notes: ['verdachte meterstand overgeslagen'] },
        ...energy.meters.slice(1),
      ],
      gas: { m3: 2.35, unit: 'm³', cost: 0.8, cost_coverage: 1, quality: 'measured', note: null },
      buckets: [
        { start: '2026-11-07T10:00:00Z', end: '2026-11-07T11:00:00Z', values: { airco: 0.2, boiler: 0.1, rest: 0.4 }, complete: true, cost: 0.2, notes: [], gas_m3: 0.1 },
        { start: '2026-11-07T11:00:00Z', end: '2026-11-07T12:00:00Z', values: { airco: 0.1, rest: 0.2 }, complete: false, cost: 0.1, notes: ['verdachte meterstand overgeslagen'], gas_m3: 0.2 },
        { start: '2026-11-08T13:00:00Z', end: '2026-11-08T14:00:00Z', values: {}, complete: false, cost: null, notes: [] },
      ],
    });
    mockApi(route((_m, url) => (url.includes('/energy/summary') ? { body: s } : undefined)));
    const { container } = render(<Providers><EnergyPage /></Providers>);
    const grid = await screen.findByTestId('meter-grid');
    expect(within(grid).getByText('geschat')).toBeInTheDocument();
    expect(within(grid).getByText('verdachte meterstand overgeslagen')).toBeInTheDocument();
    expect(within(grid).getByText(/80% met tarief/)).toBeInTheDocument();
    expect(within(screen.getByTestId('meter-rest')).getByText('afgeleid')).toBeInTheDocument();
    expect(within(screen.getByTestId('meter-gas')).getByText(/2,4 m³/)).toBeInTheDocument();

    const cols = container.querySelectorAll('.en-col');
    expect(cols[0]).toHaveClass('is-complete');
    expect(cols[1]).toHaveClass('is-incomplete');
    expect(cols[1].querySelector('.en-flag')).not.toBeNull();
    expect(cols[2]).toHaveClass('is-future');
    expect(cols[1].querySelector('[role="img"]')!.getAttribute('aria-label')).toMatch(/onvolledig.*verdachte meterstand/);
    expect(container.querySelector('.en-seg-rest')).not.toBeNull();

    expect(screen.getByText('Verbruik met een tarief: 80%')).toBeInTheDocument();
    expect(screen.getByText(/Vaste kosten \(1 dag\)/)).toBeInTheDocument();
    expect(screen.getByText('Kosten gedeeltelijk: 80% van het verbruik valt onder een tarief.')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Gas (m³)' })).toBeInTheDocument();
    expect(bucketState(s.buckets[2], NOW.getTime())).toBe('future');
  });

  it('updates the live power card from energy.live (item 36)', async () => {
    const client = newClient();
    mockApi(route());
    render(<Providers client={client}><EnergyPage /></Providers>);
    expect(await screen.findByTestId('live-total')).toHaveTextContent('850 W');
    expect(screen.getByText('Boilerstekker verouderd: overig is geschat.')).toBeInTheDocument();
    expect(within(screen.getByTestId('live-power')).getByText('verouderd')).toBeInTheDocument();
    act(() => applyWsMessage(client, 'h1', { type: 'energy.live', data: { ...live(1200), complete: true, note: null } }));
    await waitFor(() => expect(screen.getByTestId('live-total')).toHaveTextContent('1.200 W'));
    expect(client.getQueryData(qk.energyLive('h1'))).toMatchObject({ total_w: 1200 });
    expect(screen.queryByText('Boilerstekker verouderd: overig is geschat.')).not.toBeInTheDocument();
  });

  it('adds a tariff with an empty optional field, edits and deletes one', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: NOW });
    const t: Tariff = { id: 't1', valid_from: '2026-01-01', import_per_kwh: 0.28, export_per_kwh: 0.07, fixed_per_day: 1.1, import_t1_per_kwh: null, import_t2_per_kwh: null, gas_per_m3: null, source: 'handmatig' };
    let list: Tariff[] = [];
    const calls = mockApi((m, url, body) => {
      if (url.endsWith('/homes/h1/tariffs') && m === 'POST') {
        list = [{ ...t, ...(body as object), id: 't1', export_per_kwh: 0, fixed_per_day: 0 }];
        return { body: list[0] };
      }
      if (url.endsWith('/tariffs/t1') && m === 'PUT') return { body: t };
      if (url.endsWith('/tariffs/t1') && m === 'DELETE') return { status: 204 };
      return route(() => undefined, list)(m, url, body);
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Providers><EnergyPage /></Providers>);
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Tarief toevoegen' });
    // No tariff yet: the start date defaults to 1 January (in the home's zone), not today.
    expect(within(form).getByLabelText('Geldig vanaf')).toHaveValue('2026-01-01');
    await user.type(within(form).getByLabelText('Stroomprijs € per kWh'), '0,25');
    await user.click(within(form).getByRole('button', { name: 'Tarief opslaan' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ valid_from: '2026-01-01', import_per_kwh: 0.25 }));

    // Edit
    await user.click(await screen.findByRole('button', { name: 'Tarief vanaf 1 januari 2026 wijzigen' }));
    const edit = screen.getByRole('form', { name: 'Tarief vanaf 1 januari 2026 wijzigen' });
    await user.click(within(edit).getByLabelText('Aparte prijzen voor normaal (T1) en dal (T2)'));
    await user.type(within(edit).getByLabelText('Normaal (T1) € per kWh'), '0,30');
    await user.type(within(edit).getByLabelText('Dal (T2) € per kWh'), '0,22');
    await user.click(within(edit).getByRole('button', { name: 'Wijziging opslaan' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')).toMatchObject({ url: '/api/v1/tariffs/t1', body: { valid_from: '2026-01-01', import_per_kwh: 0.25, import_t1_per_kwh: 0.3, import_t2_per_kwh: 0.22 } }));

    await user.click(screen.getByRole('button', { name: 'Tarief vanaf 1 januari 2026 verwijderen' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/v1/tariffs/t1')).toBe(true));
  });

  it('blocks the tariff form until it is valid', async () => {
    const calls = mockApi(route());
    render(<Providers><EnergyPage /></Providers>);
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Tarief toevoegen' });
    await user.type(within(form).getByLabelText(/Vaste kosten/), '1,2,3');
    await user.click(within(form).getByRole('button', { name: 'Tarief opslaan' }));
    expect(within(form).getByText('Vul de stroomprijs per kWh in.')).toBeInTheDocument();
    expect(within(form).getByText(/Vaste kosten per dag: vul een getal in/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('offers export with resolution and range, and disables it offline', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: NOW });
    mockApi(route());
    const { unmount } = render(<Providers><EnergyPage /></Providers>);
    const user = userEvent.setup();
    const link = await screen.findByTestId('export-link');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/v1/homes/h1/energy/export?format=csv&from=2026-11-01&to=2026-11-09&resolution=hour');
    await user.selectOptions(screen.getByLabelText('Detail'), 'day');
    await user.selectOptions(screen.getByLabelText('Bestand'), 'json');
    expect(screen.getByTestId('export-link')).toHaveAttribute('href', '/api/v1/homes/h1/energy/export?format=json&from=2026-11-01&to=2026-11-09&resolution=day');
    unmount();

    render(<Providers connection={{ apiReachable: false, wsState: 'closed' }}><EnergyPage /></Providers>);
    const off = await screen.findByTestId('export-link');
    expect(off.tagName).toBe('BUTTON');
    expect(off).toBeDisabled();
    expect(screen.getByText('Exporteren kan alleen met verbinding met de server.')).toBeInTheDocument();
    expect(exportProblem('2026-09-01', '2026-11-09', 'raw')).toMatch(/maximaal 31 dagen/);
    expect(exportProblem('2026-10-10', '2026-11-09', 'raw')).toBeNull();
    expect(exportProblem('2026-11-09', '2026-11-01', 'hour')).toMatch(/na de einddatum/);
  });

  it('shows viewers the data but no tariff or limit controls', async () => {
    const t: Tariff = { id: 't1', valid_from: '2026-01-01', import_per_kwh: 0.28, export_per_kwh: 0.07, fixed_per_day: 1.1, import_t1_per_kwh: 0.3, import_t2_per_kwh: 0.25, gas_per_m3: 1.4, source: 'handmatig' };
    mockApi(route(() => undefined, [t]));
    render(<Providers user={viewer}><EnergyPage /></Providers>);
    expect(await screen.findByText(/T1 € 0,30 · T2 € 0,25/)).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Tarief toevoegen' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /wijzigen/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Meetgrenzen')).not.toBeInTheDocument();
  });
});
