import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClimatePage } from '../pages/Climate';
import {
  blocksToWeek,
  copyDay,
  nextChange,
  templateWeek,
  validateWeek,
  weekToBlocks,
  whenText,
  type Week,
} from '../climate/schedule';
import { validationLine } from '../climate/common';
import type { Schedule, ScheduleBlock } from '../api/types';
import { resetCommands } from '../commands';
import { admin, mockApi, Providers, viewer } from './helpers';
import { zone } from './fixtures';

const HOMES = [{ id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true }];
const workdays: ScheduleBlock[] = [
  { days: [0, 1, 2, 3, 4], start: '07:00', target_c: 21, mode: 'heat', label: 'Dag' },
  { days: [0, 1, 2, 3, 4], start: '22:30', target_c: 18, mode: null, label: 'Nacht' },
];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  resetCommands();
});

describe('schedule logic', () => {
  it('splits blocks per day and merges identical days back into one block', () => {
    const week = blocksToWeek(workdays);
    expect(week[0].map((b) => b.start)).toEqual(['07:00', '22:30']);
    expect(week[5]).toEqual([]);
    expect(week[1][1].mode).toBeNull();
    expect(weekToBlocks(week)).toEqual(workdays);
  });

  it('copies a day to other days (replacing them)', () => {
    const week = copyDay(blocksToWeek(workdays), 0, [5, 6]);
    const blocks = weekToBlocks(week);
    expect(blocks).toHaveLength(2);
    expect(blocks[0].days).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it('builds the three templates', () => {
    const o = { comfort: 21, low: 17.5, mode: null };
    expect(weekToBlocks(templateWeek('constant', o))).toEqual([{ days: [0, 1, 2, 3, 4, 5, 6], start: '00:00', target_c: 21, mode: null, label: 'Comfort' }]);
    expect(weekToBlocks(templateWeek('nachtverlaging', o)).map((b) => [b.start, b.target_c, b.days.length])).toEqual([['07:00', 21, 7], ['22:30', 17.5, 7]]);
    const pre = templateWeek('voorverwarmen', { ...o, mode: 'heat' });
    expect(pre[0].map((b) => b.start)).toEqual(['06:30', '08:30', '16:30', '22:30']);
    expect(pre[6].map((b) => b.start)).toEqual(['07:30', '23:00']);
    expect(pre[0][0].mode).toBe('heat');
  });

  it('validates like the API, in Dutch', () => {
    const week: Week = blocksToWeek(workdays);
    week[0].push({ id: 999, start: '07:00', target: '35', mode: 'dry', label: '' });
    const issues = validateWeek(week, { min_c: 16, max_c: 30, modes: ['heat', 'cool', 'auto'] });
    const msgs = issues.map((i) => i.message);
    expect(msgs).toContain('Op maandag beginnen twee blokken om 07:00. Kies een andere begintijd.');
    expect(msgs.some((m) => m.includes('35,0°') && m.includes('16°–30°') && m.includes('bereik van de airco'))).toBe(true);
    expect(msgs).toContain('Maandag, blok 3: de airco kan niet drogen.');
    expect(validateWeek(blocksToWeek(workdays), null)).toEqual([]);
  });

  it('previews the next change in the home time zone', () => {
    // Monday 2026-11-09 06:00 in Amsterdam (05:00 UTC).
    const now = new Date('2026-11-09T05:00:00Z');
    const week = blocksToWeek(workdays);
    const c = nextChange(week, now, 'Europe/Amsterdam')!;
    expect(c.block.start).toBe('07:00');
    expect(whenText(c, now, 'Europe/Amsterdam')).toBe('vandaag om 07:00');
    // Friday 23:00 → next is Monday 07:00.
    const fri = new Date('2026-11-13T22:00:00Z');
    const n2 = nextChange(week, fri, 'Europe/Amsterdam')!;
    expect(whenText(n2, fri, 'Europe/Amsterdam')).toBe('maandag om 07:00');
    expect(nextChange(blocksToWeek([]), now)).toBeNull();
  });

  it('turns pydantic validation errors into Dutch lines', () => {
    expect(validationLine({ loc: ['body', 'blocks', 0, 'start'], msg: "String should match pattern '^…$'", type: 'string_pattern_mismatch' })).toBe('Blok 1, begintijd: heeft een ongeldige notatie');
    expect(validationLine({ loc: ['body'], msg: 'Value error, Waakhond: kies 0 (uit) of minstens 15 minuten', type: 'value_error' })).toBe('Waakhond: kies 0 (uit) of minstens 15 minuten');
  });
});

function scheduleApi(initial: Schedule, put?: (body: { blocks: ScheduleBlock[] }) => { status?: number; body?: unknown }) {
  return mockApi((method, url, body) => {
    if (url.endsWith('/homes')) return { body: HOMES };
    if (url.endsWith('/homes/h1/zones')) return { body: [zone()] };
    if (url.endsWith('/zones/z1/schedule') && method === 'PUT') return put ? put(body as { blocks: ScheduleBlock[] }) : { body: { ...(body as object), profile_id: null, profile: null } };
    if (url.endsWith('/zones/z1/schedule')) return { body: initial };
    return { body: [] };
  });
}

const renderSchedule = (user = admin) => render(<Providers user={user} route="/klimaat?tab=schema"><ClimatePage /></Providers>);

async function openDay(name: RegExp) {
  await userEvent.click(await screen.findByRole('tab', { name }));
}

describe('week schedule editor (item 12)', () => {
  it('copies a day to other days and saves via PUT /zones/{id}/schedule', async () => {
    const calls = scheduleApi({ blocks: workdays, profile_id: null, profile: null });
    renderSchedule();
    await openDay(/^maandag/);
    const copy = screen.getByRole('group', { name: 'Dag kopiëren' });
    await userEvent.click(within(copy).getByRole('button', { name: 'Weekend' }));
    expect(within(copy).getByLabelText('Zaterdag')).toBeChecked();
    await userEvent.click(within(copy).getByRole('button', { name: 'Kopiëren naar 2 dagen' }));
    expect(screen.getByText('Maandag gekopieerd naar zaterdag, zondag.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Weekschema opslaan' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(put.url).toBe('/api/v1/zones/z1/schedule');
    expect(put.body).toEqual({ blocks: [
      { days: [0, 1, 2, 3, 4, 5, 6], start: '07:00', target_c: 21, mode: 'heat', label: 'Dag' },
      { days: [0, 1, 2, 3, 4, 5, 6], start: '22:30', target_c: 18, mode: null, label: 'Nacht' },
    ] });
    expect(await screen.findByText('Opgeslagen.')).toBeInTheDocument();
  });

  it('fills a template (with "houd huidige modus") and shows the next change', async () => {
    const calls = scheduleApi({ blocks: [], profile_id: null, profile: null });
    renderSchedule();
    expect(await screen.findByText('Geen blokken: de regeling doet niets en de airco houdt zijn eigen instelling.')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Sjabloon kiezen'), 'constant');
    const comfort = screen.getByLabelText('Comfort °C');
    await userEvent.clear(comfort);
    await userEvent.type(comfort, '20,5');
    await userEvent.click(screen.getByRole('button', { name: 'Sjabloon toepassen' }));
    expect(screen.getByText(/Volgende wijziging:/).parentElement).toHaveTextContent('Comfort · 20,5° · houdt de huidige modus');
    await userEvent.click(screen.getByRole('button', { name: 'Weekschema opslaan' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ blocks: [{ days: [0, 1, 2, 3, 4, 5, 6], start: '00:00', target_c: 20.5, mode: null, label: 'Comfort' }] });
  });

  it('shows validation messages and does not save an invalid schedule; shows the API 422 message', async () => {
    let puts = 0;
    const calls = scheduleApi({ blocks: workdays, profile_id: null, profile: null }, () => {
      puts++;
      return { status: 422, body: { detail: { code: 'validation_error', message: 'Blok 1: 29 °C ligt buiten het bereik van de airco (16–28 °C).' } } };
    });
    renderSchedule();
    await openDay(/^maandag/);
    await userEvent.click(screen.getByRole('button', { name: 'Blok toevoegen' }));
    fireEvent.change(screen.getByLabelText('Begintijd blok 3'), { target: { value: '07:00' } });
    await userEvent.click(screen.getByRole('button', { name: 'Weekschema opslaan' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Op maandag beginnen twee blokken om 07:00. Kies een andere begintijd.');
    expect(screen.getByLabelText('Begintijd blok 3')).toHaveAttribute('aria-invalid', 'true');
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);

    // Fix it; the server still refuses with its own Dutch message.
    fireEvent.change(screen.getByLabelText('Begintijd blok 3'), { target: { value: '12:00' } });
    const target = screen.getByLabelText('Doel °C blok 3');
    await userEvent.clear(target);
    await userEvent.type(target, '29');
    await userEvent.click(screen.getByRole('button', { name: 'Weekschema opslaan' }));
    expect(await screen.findByText('Blok 1: 29 °C ligt buiten het bereik van de airco (16–28 °C).')).toBeInTheDocument();
    expect(puts).toBe(1);
  });

  it('warns that saving detaches the zone from its profile', async () => {
    scheduleApi({ blocks: workdays, profile_id: 'p1', profile: 'Winter' });
    renderSchedule();
    expect(await screen.findByText(/volgt nu profiel Winter\. Sla je hier een schema op/)).toBeInTheDocument();
  });

  it('is read-only for viewers', async () => {
    scheduleApi({ blocks: workdays, profile_id: null, profile: null });
    renderSchedule(viewer);
    await openDay(/^maandag/);
    expect(screen.getByText('Alleen een beheerder kan het schema wijzigen.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Weekschema opslaan' })).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Blokken op maandag' })).toHaveTextContent('07:00 · Dag · 21,0° · zet de airco op verwarmen');
    expect(screen.queryByRole('tab', { name: 'Instellingen' })).not.toBeInTheDocument();
  });
});
