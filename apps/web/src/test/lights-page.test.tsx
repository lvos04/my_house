import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LightsPage } from '../pages/Lights';
import { applyCommandUpdate, resetCommands } from '../commands';
import type { HueButton, Lights } from '../api/types';
import { buildNames, describeStep, draftErrors, draftFromMapping, emptyStep, mappingBody, mappingRows } from '../lights/mapping';
import { mockApi, Providers, viewer } from './helpers';
import { group, zone } from './fixtures';

// Real Hue resource ids are 36 characters (the old API allowed only 32).
const BTN1 = '3f9c1a2e-7b4d-4c8e-9a6f-0d2b5e8c1a7f';
const BTN2 = 'b8e2d4f6-1a3c-4e5b-8d7f-9c0a2b4d6e8f';
const DEV = 'aaaaaaaa-0000-4000-8000-000000000001';
const MAP1 = 'cccccccc-0000-4000-8000-000000000009';
expect(BTN1).toHaveLength(36);

const lights: Lights = {
  groups: [
    group({ id: 'gall', name: 'Alle lampen', all_lights: true, hidden: true, lights_on: 3, lights_total: 5, lights_reachable: 4 }),
    group({ id: 'g1', name: 'Keuken', lights_on: 1, lights_total: 3, lights_reachable: 2, scene: 'Helder', brightness: 100 }),
    group({
      id: 'g2',
      name: 'Woonkamer',
      on: false,
      lights_on: 0,
      lights_total: 2,
      lights_reachable: 2,
      scene: null,
      brightness: 50,
      last_command: { id: 'old', action: 'set_group', status: 'failed', detail: 'Hue Bridge meldt: device has communication issues', at: new Date().toISOString() },
    }),
  ],
  scenes: [
    { id: 's1', name: 'Helder', group_id: 'g1', active: true },
    { id: 's2', name: 'Avond', group_id: 'g1', active: false },
  ],
  lights: [
    { id: 'l1', name: 'Spot 1', on: true, brightness: 100, reachable: true, model: null, last_command: null },
    { id: 'l2', name: 'Bureaulamp', on: false, brightness: null, reachable: false, model: 'LCT015', last_command: null },
  ],
};

const hueButton = (over: Partial<HueButton> = {}): HueButton => ({
  id: DEV,
  name: 'Dimmer gang',
  model: 'RWL022',
  online: true,
  last_seen: null,
  battery: 80,
  buttons: [
    { id: BTN1, control_id: 1, type: 'button', events: ['initial_press', 'short_release', 'long_press'], bridge_rule: true },
    { id: BTN2, control_id: 2, type: 'button', events: ['short_release'], bridge_rule: false },
  ],
  events: ['initial_press', 'short_release', 'long_press'],
  bridge_rules: [BTN1],
  bridge_rule_names: ['Gang aan/uit'],
  last_event: null,
  mapping: [
    {
      id: MAP1,
      button: BTN1,
      event: 'short_release',
      mode: 'lights',
      action: { steps: [{ type: 'light', device_id: 'g1', action: 'set_group', params: { on: true, brightness: 80 } }] },
      combined_confirmed: false,
      enabled: true,
      conflict: 'De Hue Bridge voert voor deze knop zelf al een actie uit; dubbele bediening mogelijk.',
    },
  ],
  ...over,
});

function route(buttons: HueButton[] = [hueButton()]) {
  return (m: string, url: string): ReturnType<Parameters<typeof mockApi>[0]> => {
    if (url.endsWith('/homes/h1/lights')) return { body: lights };
    if (url.endsWith('/homes/h1/buttons')) return { body: buttons };
    if (url.endsWith('/homes/h1/zones')) return { body: [zone()] };
    if (url.startsWith('/api/v1/lights/groups/') && m === 'POST') return { status: 202, body: { command_id: 'c1', status: 'queued' } };
    return { body: [] };
  };
}

beforeEach(() => resetCommands());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('lights page (item 27)', () => {
  it('shows x of y on, the active scene, unreachable lamps, a failed command with its reason, and "Alle lampen" last', async () => {
    mockApi(route());
    render(<Providers><LightsPage /></Providers>);
    const keuken = await screen.findByTestId('group-g1');
    expect(within(keuken).getByText('1 van 3 aan · scène Helder')).toBeInTheDocument();
    expect(within(keuken).getByText(/1 lamp niet bereikbaar/)).toBeInTheDocument();
    expect(within(keuken).getByRole('button', { name: 'Helder' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(keuken).getByRole('button', { name: 'Avond' })).toHaveAttribute('aria-pressed', 'false');
    const woon = screen.getByTestId('group-g2');
    expect(within(woon).getByRole('alert')).toHaveTextContent('Laatste opdracht: Mislukt: Hue Bridge meldt: device has communication issues');
    expect(screen.getByText(/Niet bereikbaar: Bureaulamp/)).toBeInTheDocument();
    // "Alle lampen" comes after the rooms, in its own section.
    const cards = screen.getAllByRole('article').filter((a) => a.dataset.testid?.startsWith('group-'));
    expect(cards.map((c) => c.dataset.testid)).toEqual(['group-g1', 'group-g2', 'group-gall']);
    expect(within(screen.getByRole('region', { name: 'Hele huis' })).getByText('Alle lampen')).toBeInTheDocument();
  });

  it('sends brightness only on release, never while dragging or on focus, and shows a failure with the reason', async () => {
    const calls = mockApi(route());
    render(<Providers><LightsPage /></Providers>);
    const slider = await screen.findByLabelText('Helderheid Keuken');
    const posts = () => calls.filter((c) => c.method === 'POST' && c.url === '/api/v1/lights/groups/g1');

    fireEvent.focus(slider);
    fireEvent.keyUp(slider, { key: 'Tab' });
    fireEvent.pointerDown(slider);
    fireEvent.change(slider, { target: { value: '60' } });
    fireEvent.change(slider, { target: { value: '40' } });
    await new Promise((r) => setTimeout(r, 50));
    expect(posts()).toHaveLength(0);
    expect(slider).toHaveAttribute('aria-valuetext', '40%');

    fireEvent.pointerUp(slider);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].body).toEqual({ brightness: 40 });
    // Releasing again without a change sends nothing.
    fireEvent.pointerUp(slider);

    // Keyboard: one value after the last key, not one per key press.
    fireEvent.change(slider, { target: { value: '41' } });
    fireEvent.keyUp(slider, { key: 'ArrowRight' });
    fireEvent.change(slider, { target: { value: '42' } });
    fireEvent.keyUp(slider, { key: 'ArrowRight' });
    expect(posts()).toHaveLength(1);
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].body).toEqual({ brightness: 42 });

    act(() => applyCommandUpdate({ id: 'c1', status: 'failed', detail: 'Hue Bridge meldt: device has communication issues' }));
    expect(within(screen.getByTestId('group-g1')).getByRole('alert')).toHaveTextContent('Helderheid 42%: Mislukt: Hue Bridge meldt: device has communication issues');
  });

  it('turns a group that is off on when the brightness is set', async () => {
    const calls = mockApi(route());
    render(<Providers><LightsPage /></Providers>);
    const slider = await screen.findByLabelText('Helderheid Woonkamer');
    fireEvent.change(slider, { target: { value: '30' } });
    fireEvent.pointerUp(slider);
    await waitFor(() => expect(calls.find((c) => c.url === '/api/v1/lights/groups/g2')?.body).toEqual({ on: true, brightness: 30 }));
  });

  it('shows viewers the state without controls', async () => {
    mockApi(route());
    render(<Providers user={viewer}><LightsPage /></Providers>);
    const keuken = await screen.findByTestId('group-g1');
    expect(within(keuken).queryByRole('slider')).not.toBeInTheDocument();
    expect(within(keuken).queryByRole('button')).not.toBeInTheDocument();
    expect(await screen.findByText('Dimmer gang')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Simuleer/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /bewerken/ })).not.toBeInTheDocument();
  });
});

describe('button mapping editor (item 25)', () => {
  it('lists rows per button and Dutch event with conflicts, and simulates a press with the 36-char id', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith(`/buttons/${DEV}/simulate`))
        return {
          body: {
            button: BTN1,
            event: 'short_release',
            would_run: true,
            bridge_rule: true,
            message: 'Een druk voert de getoonde stappen uit. Let op: de Hue Bridge voert voor deze knop zelf ook een actie uit.',
            mappings: [
              { mapping_id: MAP1, mode: 'lights', conflict: 'De Hue Bridge voert voor deze knop zelf al een actie uit; dubbele bediening mogelijk.', steps: [{ type: 'light', result: 'would_run', reason: null, action: 'set_group', params: { on: true, brightness: 80 }, device_id: 'g1', device_name: 'Keuken' }] },
            ],
          },
        };
      return route()(m, url);
    });
    render(<Providers><LightsPage /></Providers>);
    const row = await screen.findByTestId(`row-${BTN1}-short_release`);
    expect(within(row).getByText('Kort indrukken')).toBeInTheDocument();
    expect(within(row).getByText('Keuken: aan, 80%')).toBeInTheDocument();
    expect(within(row).getByText(/dubbele bediening mogelijk/)).toBeInTheDocument();
    expect(screen.getByTestId(`row-${BTN1}-initial_press`)).toHaveTextContent('Indrukken');
    expect(screen.getByTestId(`row-${BTN1}-long_press`)).toHaveTextContent('Lang indrukken');
    expect(screen.getByRole('region', { name: 'Knop 1' })).toHaveTextContent('Ook een regel op de Hue Bridge');

    await userEvent.setup().click(within(row).getByRole('button', { name: 'Simuleer Knop 1 · Kort indrukken' }));
    const sim = await within(row).findByTestId('simulation');
    expect(calls.find((c) => c.url.endsWith('/simulate'))?.body).toEqual({ button: BTN1, event: 'short_release' });
    expect(sim).toHaveTextContent('Wordt uitgevoerd');
    expect(sim).toHaveTextContent('Keuken: aan, 80%');
    expect(sim).toHaveTextContent('Hue Bridge voert voor deze knop zelf ook een actie uit');
  });

  it('creates a lights mapping, and a combined one only after explicit confirmation', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith(`/buttons/${DEV}/mappings`) && m === 'POST') return { body: hueButton() };
      return route()(m, url);
    });
    render(<Providers><LightsPage /></Providers>);
    const user = userEvent.setup();
    const row = await screen.findByTestId(`row-${BTN2}-short_release`);
    await user.click(within(row).getByRole('button', { name: 'Koppeling maken voor Knop 2 · Kort indrukken' }));
    const form = within(row).getByRole('form', { name: 'Koppeling Knop 2 · Kort indrukken' });
    const step1 = within(form).getByTestId('step-1');
    await user.selectOptions(within(step1).getByLabelText('Lamp, groep of scène'), 'g2');
    await user.type(within(step1).getByLabelText('Helderheid (1–100 %)'), '60');
    await user.click(within(form).getByRole('button', { name: 'Koppeling opslaan' }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/mappings'))?.body).toEqual({
        button: BTN2,
        event: 'short_release',
        mode: 'lights',
        action: { steps: [{ type: 'light', device_id: 'g2', action: 'set_group', params: { on: true, brightness: 60 } }] },
        combined_confirmed: false,
        enabled: true,
      }),
    );

    // Combined: light + climate, blocked until confirmed.
    const row2 = screen.getByTestId(`row-${BTN1}-long_press`);
    await user.click(within(row2).getByRole('button', { name: 'Koppeling maken voor Knop 1 · Lang indrukken' }));
    const form2 = within(row2).getByRole('form', { name: 'Koppeling Knop 1 · Lang indrukken' });
    await user.click(within(form2).getByRole('radio', { name: /Licht en klimaat/ }));
    await user.selectOptions(within(within(form2).getByTestId('step-1')).getByLabelText('Lamp, groep of scène'), 's2');
    const zoneStep = within(form2).getByTestId('step-2');
    await user.selectOptions(within(zoneStep).getByLabelText('Klimaatzone'), 'z1');
    await user.selectOptions(within(zoneStep).getByLabelText('Modus'), 'heat');
    await user.type(within(zoneStep).getByLabelText('Doeltemperatuur (°C)'), '21,5');
    expect(within(form2).getByText(/De Hue Bridge voert voor deze knop zelf al een actie uit \(Gang aan\/uit\)/)).toBeInTheDocument();
    await user.click(within(form2).getByRole('button', { name: 'Koppeling opslaan' }));
    expect(within(form2).getByRole('alert')).toHaveTextContent('Bevestig dat deze knop bewust zowel licht als airco bedient.');
    expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/mappings'))).toHaveLength(1);
    await user.click(within(form2).getByLabelText(/Ik wil bewust dat één druk/));
    await user.click(within(form2).getByRole('button', { name: 'Koppeling opslaan' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/mappings'))).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/mappings'))[1].body).toEqual({
      button: BTN1,
      event: 'long_press',
      mode: 'combined',
      action: { steps: [{ type: 'light', device_id: 's2', action: 'recall_scene' }, { type: 'zone', zone_id: 'z1', mode: 'heat', target_c: 21.5 }] },
      combined_confirmed: true,
      enabled: true,
    });
  });

  it('edits an existing mapping with PATCH and shows server conflicts per row', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith(`/button-mappings/${MAP1}`) && m === 'PATCH')
        return { status: 409, body: { detail: { code: 'conflict', message: 'Knopkoppeling heeft conflicten.', conflicts: [{ index: 0, message: 'Meerdere acties op hetzelfde knopevent.' }] } } };
      return route()(m, url);
    });
    render(<Providers><LightsPage /></Providers>);
    const user = userEvent.setup();
    const row = await screen.findByTestId(`row-${BTN1}-short_release`);
    await user.click(within(row).getByRole('button', { name: 'Koppeling Knop 1 · Kort indrukken bewerken' }));
    const form = within(row).getByRole('form');
    await user.click(within(form).getByLabelText('Koppeling actief'));
    await user.click(within(form).getByRole('button', { name: 'Koppeling opslaan' }));
    await waitFor(() => expect(within(form).getByRole('alert')).toHaveTextContent('Meerdere acties op hetzelfde knopevent.'));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({
      mode: 'lights',
      action: { steps: [{ type: 'light', device_id: 'g1', action: 'set_group', params: { on: true, brightness: 80 } }] },
      combined_confirmed: false,
      enabled: false,
    });
  });
});

describe('mapping model', () => {
  const names = buildNames(lights, [zone()]);
  it('checks steps like the server and describes them in Dutch', () => {
    const d = draftFromMapping(null);
    expect(draftErrors(d, names)).toEqual(['Stap 1: kies een lamp, groep of scène.']);
    const zs = { ...emptyStep('zone'), zone_id: 'z1', target_c: '40' };
    expect(draftErrors({ ...d, mode: 'lights', steps: [zs] }, names)).toEqual(['Stap 1: een lichtkoppeling mag alleen lamp-, groep- en scènestappen bevatten.']);
    expect(draftErrors({ ...d, mode: 'climate', steps: [zs] }, names)).toEqual(['Stap 1: doeltemperatuur moet tussen 5 en 35 °C liggen.']);
    const resume = { ...emptyStep('zone'), zone_id: 'z1', zoneAction: 'resume' as const };
    expect(mappingBody({ ...d, mode: 'climate', steps: [resume] }, BTN1, 'repeat', names).action).toEqual({ steps: [{ type: 'zone', zone_id: 'z1', action: 'resume' }] });
    expect(describeStep({ type: 'zone', zone_id: 'z1', mode: 'cool', target_c: 22, fan: 'low', override_minutes: 60 }, names)).toBe('Woonkamer: Koelen, 22°, ventilator laag, 60 min');
    expect(describeStep({ type: 'light', device_id: 'g1', action: 'toggle' }, names)).toBe('Keuken: aan/uit wisselen');
    expect(describeStep({ type: 'light', device_id: 's1', action: 'recall_scene' }, names)).toBe('Scène Helder (Keuken)');
  });

  it('builds rows from mappings and the last press when the bridge did not list the buttons', () => {
    const rows = mappingRows(hueButton({ buttons: [], last_event: { button: BTN2, event: 'long_press', at: new Date().toISOString() } }));
    expect(rows.map((r) => `${r.inputLabel}|${r.event}|${r.mappings.length}`)).toEqual([
      'Knop 1|initial_press|0',
      'Knop 1|short_release|1',
      'Knop 1|long_press|0',
      'Knop 2|initial_press|0',
      'Knop 2|short_release|0',
      'Knop 2|long_press|0',
    ]);
  });
});
