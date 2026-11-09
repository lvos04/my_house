import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClimatePage, ZoneSettings } from '../pages/Climate';
import { ExperimentResultView, OptimizationPage, experimentProblems } from '../pages/Optimization';
import { policyChanges } from '../climate/PolicyForm';
import type { Experiment, ExperimentResult, Profile } from '../api/types';
import { resetCommands } from '../commands';
import { admin, mockApi, Providers, viewer } from './helpers';
import { device, policy, zone } from './fixtures';

const HOMES = [{ id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true }];
const winter: Profile = {
  id: 'p1', home_id: 'h1', name: 'Winter', target_c: 21, band_c: 0.5, mode: 'heat', energy_priority: 'balanced',
  schedule: [{ days: [0, 1, 2, 3, 4, 5, 6], start: '07:00', target_c: 21, mode: null, label: 'Dag' }], zone_ids: [], created_at: null,
};
const zoneBlocks = [{ days: [0, 1, 2, 3, 4], start: '06:30', target_c: 20, mode: 'heat' as const, label: 'Opwarmen' }];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  resetCommands();
});

describe('comfort profiles (item 45)', () => {
  it('creates a profile from the zone schedule, applies one to the zone and shows a refused delete', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const calls = mockApi((m, url, body) => {
      if (url.endsWith('/homes')) return { body: HOMES };
      if (url.endsWith('/homes/h1/zones')) return { body: [zone()] };
      if (url.endsWith('/zones/z1/schedule')) return { body: { blocks: zoneBlocks, profile_id: null, profile: null } };
      if (url.endsWith('/homes/h1/profiles') && m === 'POST') return { body: { ...winter, ...(body as object), id: 'p2' } };
      if (url.endsWith('/homes/h1/profiles')) return { body: [winter] };
      if (url.endsWith('/zones/z1/profile')) return { body: zone({ profile_id: 'p1', profile: 'Winter' }) };
      if (url.endsWith('/profiles/p1') && m === 'DELETE') return { status: 409, body: { detail: { code: 'conflict', message: "Experiment 'Nacht' gebruikt dit profiel nog." } } };
      return { body: [] };
    });
    render(<Providers user={admin} route="/klimaat?tab=profielen"><ClimatePage /></Providers>);
    const card = (await screen.findByRole('heading', { name: 'Winter' })).closest('article')!;
    expect(within(card).getByText('Modus verwarmen')).toBeInTheDocument();
    expect(within(card).getByText(/Weekschema met 1 blok\./)).toBeInTheDocument();

    await userEvent.click(within(card).getByRole('button', { name: 'Toepassen op Woonkamer' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/zones/z1/profile'))?.body).toEqual({ profile_id: 'p1' }));
    expect(await screen.findByText('Profiel toegepast op Woonkamer.')).toBeInTheDocument();

    await userEvent.click(within(card).getByRole('button', { name: 'Verwijderen' }));
    expect(await within(card).findByText("Experiment 'Nacht' gebruikt dit profiel nog.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Nieuw profiel' }));
    const form = screen.getByRole('form', { name: 'Nieuw comfortprofiel' });
    await userEvent.type(within(form).getByLabelText('Naam van het profiel'), 'Thuiswerkdag');
    await userEvent.click(within(form).getByLabelText(/Zuinig/));
    await waitFor(() => expect(within(form).getByLabelText(/Weekschema van Woonkamer overnemen/)).toBeEnabled());
    await userEvent.click(within(form).getByLabelText(/Weekschema van Woonkamer overnemen/));
    await userEvent.click(within(form).getByRole('button', { name: 'Profiel aanmaken' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/profiles'))).toBe(true));
    expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/profiles'))!.body).toEqual({
      name: 'Thuiswerkdag', target_c: 21, band_c: 0.5, mode: null, energy_priority: 'saving', schedule: zoneBlocks,
    });
  });

  it('viewers only see the profiles', async () => {
    mockApi((_m, url) => {
      if (url.endsWith('/homes')) return { body: HOMES };
      if (url.endsWith('/homes/h1/zones')) return { body: [zone()] };
      if (url.endsWith('/homes/h1/profiles')) return { body: [winter] };
      return { body: { blocks: [] } };
    });
    render(<Providers user={viewer} route="/klimaat?tab=profielen"><ClimatePage /></Providers>);
    expect(await screen.findByRole('heading', { name: 'Winter' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Toepassen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nieuw profiel' })).not.toBeInTheDocument();
  });
});

describe('regelgrenzen (item 45: energy priority, watchdog, plausibility)', () => {
  it('validates and sends only the changed fields', async () => {
    expect(policyChanges(policy, { band_c: '0,5', min_interval_s: '15', max_step_c: '1', max_starts_per_hour: '3', stale_after_s: '30', max_offset_c: '1,5', override_default_minutes: '120', watchdog_minutes: '60', watchdog_min_progress_c: '0,3', plausible_min_c: '5', plausible_max_c: '40' }, 'balanced')).toEqual({ body: {}, problems: [] });
    const calls = mockApi((m, url, body) => (m === 'PUT' && url.endsWith('/zones/z1/policy') ? { body: zone({ policy: { ...policy, ...(body as object) } }) } : { body: [] }));
    render(<Providers user={admin}><ZoneSettings zone={zone()} homeId="h1" /></Providers>);
    const wd = screen.getByLabelText('Waakhond na (min, 0 = uit)');
    await userEvent.clear(wd);
    await userEvent.type(wd, '10');
    const lo = screen.getByLabelText('Ondergrens °C');
    await userEvent.clear(lo);
    await userEvent.type(lo, '45');
    await userEvent.click(screen.getByRole('button', { name: 'Regelgrenzen opslaan' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Waakhond: kies 0 (uit) of minstens 15 minuten.');
    expect(alert).toHaveTextContent('Ondergrens °C: kies een waarde van -30 tot 30.');
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);

    await userEvent.clear(wd);
    await userEvent.type(wd, '30');
    await userEvent.clear(lo);
    await userEvent.type(lo, '8,5');
    await userEvent.click(screen.getByLabelText(/Zuinig/));
    await userEvent.click(screen.getByRole('button', { name: 'Regelgrenzen opslaan' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ watchdog_minutes: 30, plausible_min_c: 8.5, energy_priority: 'saving' });
    expect(await screen.findByText('Regelgrenzen opgeslagen.')).toBeInTheDocument();
  });
});

describe('Optimalisatie (item 45)', () => {
  const sensor = device({ id: 's9', name: 'Sensor tuin', allowed_roles: ['outdoor_temp'] });
  const exp: Experiment = {
    id: 'e1', name: 'Nachtverlaging', zone_id: 'z1', status: 'paused', auto_apply: true, current_variant: null,
    paused_reason: 'De zone werd met de hand bediend.', created_at: '2026-11-01T00:00:00Z',
    variants: [
      { label: 'Constant', start: '2026-11-01T00:00:00Z', end: '2026-11-08T00:00:00Z', profile_id: 'p1' },
      { label: 'Nacht', start: '2026-11-08T00:00:00Z', end: '2026-11-15T00:00:00Z', profile_id: 'p1' },
    ],
  };

  it('binds the outdoor sensor, resumes a paused experiment and requires profiles for auto_apply', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith('/homes/h1/zones')) return { body: [zone()] };
      if (url.endsWith('/homes/h1/climate/roles')) return { body: [{ role: 'outdoor_temp', label: 'Buitentemperatuur', device_id: null, device_name: null }] };
      if (url.endsWith('/homes/h1/devices')) return { body: [sensor, device({ id: 'p1m', kind: 'p1_meter', name: 'P1', allowed_roles: ['grid'] })] };
      if (url.endsWith('/devices/s9/bind')) return { body: { ...sensor, roles: ['outdoor_temp'] } };
      if (url.endsWith('/homes/h1/profiles')) return { body: [winter] };
      if (url.endsWith('/homes/h1/experiments') && m === 'POST') return { body: { ...exp, id: 'e2', status: 'defined' } };
      if (url.endsWith('/homes/h1/experiments')) return { body: [exp] };
      if (url.endsWith('/experiments/e1/resume')) return { body: { ...exp, status: 'running', paused_reason: null } };
      if (url.endsWith('/zones/z1/adaptive')) return { body: { enabled: false, baseline: { samples: 0, running_samples: 0, required: 192, since: null, ready: false }, max_offset_c: 1.5, models: [] } };
      return { body: [] };
    });
    render(<Providers user={admin}><OptimizationPage /></Providers>);
    const pickSensor = await screen.findByLabelText('Buitensensor kiezen');
    expect(within(pickSensor).queryByRole('option', { name: 'P1' })).not.toBeInTheDocument();
    await userEvent.selectOptions(pickSensor, 's9');
    await userEvent.click(screen.getByRole('button', { name: 'Als buitensensor gebruiken' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/devices/s9/bind'))?.body).toEqual({ role: 'outdoor_temp' }));

    const card = screen.getByRole('heading', { name: 'Nachtverlaging' }).closest('article')!;
    expect(within(card).getByText('Gepauzeerd', { selector: '.pill' })).toBeInTheDocument();
    expect(within(card).getByText('Profielen automatisch')).toBeInTheDocument();
    expect(within(card).getByText(/De zone werd met de hand bediend\./)).toBeInTheDocument();
    expect(within(card).getAllByText(/profiel Winter/)).toHaveLength(2);
    await userEvent.click(within(card).getByRole('button', { name: 'Hervatten' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/experiments/e1/resume'))).toBe(true));

    await userEvent.type(screen.getByLabelText('Naam'), 'Profieltest');
    const starts = screen.getAllByLabelText('Begin');
    const ends = screen.getAllByLabelText('Einde');
    await userEvent.type(starts[0], '2026-11-20T00:00');
    await userEvent.type(ends[0], '2026-11-27T00:00');
    await userEvent.type(starts[1], '2026-11-27T00:00');
    await userEvent.type(ends[1], '2026-12-04T00:00');
    await userEvent.click(screen.getByLabelText('Profielen automatisch toepassen'));
    await userEvent.click(screen.getByRole('button', { name: 'Experiment aanmaken' }));
    expect(await screen.findByText('Automatisch toepassen: kies voor elke variant een profiel.')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/experiments'))).toBe(false);
    const profileSelects = screen.getAllByLabelText('Profiel');
    await userEvent.selectOptions(profileSelects[0], 'p1');
    await userEvent.selectOptions(profileSelects[1], 'p1');
    await userEvent.click(screen.getByRole('button', { name: 'Experiment aanmaken' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/experiments'))).toBe(true));
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/homes/h1/experiments'))!.body as { auto_apply: boolean; variants: { profile_id: string }[] };
    expect(post.auto_apply).toBe(true);
    expect(post.variants.map((v) => v.profile_id)).toEqual(['p1', 'p1']);
  });

  it('checks overlapping periods for auto_apply', () => {
    const v = (start: string, end: string) => ({ label: 'x', start, end, profileId: 'p1' });
    expect(experimentProblems([v('2026-11-01T00:00', '2026-11-08T00:00'), v('2026-11-07T00:00', '2026-11-14T00:00')], true)).toEqual(['Automatisch toepassen: de perioden mogen niet overlappen.']);
    expect(experimentProblems([v('2026-11-01T00:00', '2026-11-08T00:00'), v('2026-11-07T00:00', '2026-11-14T00:00')], false)).toEqual([]);
  });

  it('shows the comfort score, the other zone and the confounders in the result', () => {
    const r: ExperimentResult = {
      id: 'e', name: 'x', zone_id: 'z1', variants: [], status: 'finished', auto_apply: true, current_variant: null, paused_reason: null,
      outdoor: { device_id: 's9', name: 'Sensor tuin' },
      confounders: ['Slaapkamer hangt aan dezelfde buitenunit; de airco-meter telt het verbruik van beide ruimtes.'],
      verdict: 'indicatief',
      reasons: ['De andere ruimte op dezelfde buitenunit werd per periode anders ingesteld; dat telt mee in het verbruik.'],
      rows: [
        { variant: 'Constant', days: 7, kwh_per_day: 4, vs_first_pct: 0, mean_abs_dev_c: 0.3, starts: 4, outdoor_mean_c: 6, comfort_pct: 92.4, other_zone_mean_target_c: 19, notes: [] },
        { variant: 'Nacht', days: 7, kwh_per_day: 3.6, vs_first_pct: -10, mean_abs_dev_c: 0.4, starts: 6, outdoor_mean_c: 6.5, comfort_pct: 81, other_zone_mean_target_c: 20.5, notes: ['Geen temperatuurmetingen in deze periode.'] },
      ],
    };
    render(<ExperimentResultView r={r} />);
    expect(screen.getByText('92 % van de tijd')).toBeInTheDocument();
    expect(screen.getByText('81 % van de tijd')).toBeInTheDocument();
    expect(screen.getByText('20,5')).toBeInTheDocument();
    expect(screen.getByText(/Slaapkamer hangt aan dezelfde buitenunit/)).toBeInTheDocument();
    expect(screen.getByText('Nacht: Geen temperatuurmetingen in deze periode.')).toBeInTheDocument();
    expect(screen.getByText(/Buitensensor: Sensor tuin\./)).toBeInTheDocument();
    expect(screen.queryByText(/besparing/i)).not.toBeInTheDocument();
  });
});
