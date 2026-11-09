import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ConnectionProvider, HomeProvider, PendingProvider } from '../state';
import { UserContext } from '../user';
import { ExperimentResultView, OptimizationPage, activateLabel, localInputToIso } from '../pages/Optimization';
import type { AdaptiveModel, ExperimentResult } from '../api/types';
import { zone } from './fixtures';

function wrap(children: ReactNode) {
  localStorage.setItem('shc.home', 'h1');
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectionProvider initial={{ apiReachable: true, wsState: 'open' }}>
        <PendingProvider>
          <UserContext.Provider value={{ id: 'u1', username: 'admin', role: 'admin' }}>
            <HomeProvider>{children}</HomeProvider>
          </UserContext.Provider>
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}

const model = (version: number, active = false, usable = true): AdaptiveModel => ({
  id: `m${version}`, version, active, created_at: null, params: { k_per_h: 0.42, c_per_h: -0.1, n: 300, r2: 0.81, usable },
});

function mockApi(route: (method: string, url: string, body: unknown) => { status?: number; body: unknown } | undefined) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    const method = init.method ?? 'GET';
    calls.push({ method, url, body });
    const r = route(method, url, body) ?? { body: [] };
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'content-type': 'application/json' } });
  }));
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('Optimalisatie', () => {
  it('labels rollback and converts datetime-local to UTC ISO', () => {
    const models = [model(3), model(2, true), model(1)];
    expect(activateLabel(models[0], models)).toBe('Activeren');
    expect(activateLabel(models[2], models)).toBe('Terugzetten');
    expect(activateLabel(model(1), [model(1)])).toBe('Activeren');
    expect(localInputToIso('2026-11-01T22:00')).toBe(new Date(2026, 10, 1, 22, 0).toISOString());
  });

  it('shows baseline, models, fit error, rollback and experiment creation', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith('/homes/h1/zones')) return { body: [zone()] };
      if (url.endsWith('/zones/z1/adaptive'))
        return { body: { enabled: true, baseline: { samples: 500, running_samples: 120, required: 192, since: null, ready: false }, max_offset_c: 1.5, models: [model(2, true), model(1)] } };
      if (url.endsWith('/adaptive/fit')) return { status: 409, body: { detail: { code: 'insufficient_data', message: 'Nog te weinig meetgegevens.' } } };
      if (url.endsWith('/adaptive/activate')) return { body: { enabled: true, baseline: { samples: 500, running_samples: 120, required: 192, since: null, ready: false }, max_offset_c: 1.5, models: [model(2), model(1, true)] } };
      if (url.endsWith('/homes/h1/experiments') && m === 'POST') return { body: { id: 'e1', name: 'Test', variants: [], status: 'defined' } };
      if (url.endsWith('/homes/h1/experiments')) return { body: [] };
      return undefined;
    });
    render(wrap(<OptimizationPage />));
    const user = userEvent.setup();
    expect(await screen.findByText('120 van 192 kwartieren')).toBeInTheDocument();
    expect(screen.getByText(/Max\. bijsturing ±1,5°, harde grenzen blijven gelden, standaard uit\./)).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '120');

    await user.click(screen.getByRole('button', { name: 'Model berekenen' }));
    expect(await screen.findByText('Nog te weinig meetgegevens.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Terugzetten' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/adaptive/activate'))?.body).toEqual({ model_id: 'm1' }));

    await user.type(screen.getByLabelText('Naam'), 'Nacht');
    const starts = screen.getAllByLabelText('Begin');
    const ends = screen.getAllByLabelText('Einde');
    await user.type(starts[0], '2026-11-01T00:00');
    await user.type(ends[0], '2026-11-08T00:00');
    await user.type(starts[1], '2026-11-08T00:00');
    await user.type(ends[1], '2026-11-15T00:00');
    await user.click(screen.getByRole('button', { name: 'Experiment aanmaken' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/experiments'))).toBe(true));
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/experiments'))!.body as { zone_id: string; variants: { start: string }[] };
    expect(post.zone_id).toBe('z1');
    expect(post.variants).toHaveLength(2);
    expect(post.variants[0].start).toBe(new Date(2026, 10, 1).toISOString());
  });

  it('only says besparing when the verdict is vergelijkbaar', () => {
    const rows = [
      { variant: 'Constant', days: 7, kwh_per_day: 4, vs_first_pct: 0, mean_abs_dev_c: 0.3, starts: 4, outdoor_mean_c: 6, comfort_pct: null, other_zone_mean_target_c: null, notes: [] },
      { variant: 'Nacht', days: 7, kwh_per_day: 3.6, vs_first_pct: -10, mean_abs_dev_c: 0.4, starts: 6, outdoor_mean_c: 6.5, comfort_pct: null, other_zone_mean_target_c: null, notes: [] },
    ];
    const base: ExperimentResult = { id: 'e', name: 'x', zone_id: 'z1', variants: [], status: 'defined', auto_apply: false, current_variant: null, paused_reason: null, outdoor: null, confounders: [], verdict: 'indicatief', reasons: ['Geen buitentemperatuur gemeten; weersinvloed onbekend.'], rows };
    const { unmount } = render(<ExperimentResultView r={base} />);
    expect(screen.getByText('Indicatieve vergelijking')).toBeInTheDocument();
    expect(screen.getByText('Geen buitentemperatuur gemeten; weersinvloed onbekend.')).toBeInTheDocument();
    expect(screen.queryByText(/besparing/i)).not.toBeInTheDocument();
    expect(screen.getByText('−10,0%')).toBeInTheDocument();
    unmount();
    render(<ExperimentResultView r={{ ...base, verdict: 'vergelijkbaar', reasons: [] }} />);
    expect(screen.getByText('Vergelijkbare meting')).toBeInTheDocument();
    expect(screen.getByText('10,0% besparing')).toBeInTheDocument();
  });
});
