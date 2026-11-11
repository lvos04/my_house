// Grafiek "Laatste 24 uur": tabel per uur en bediening met toetsenbord en aanwijzer (besluit gebruiker C09,
// Peil 1.1.1 §10, ADR-0013). Een uur zonder meting heet "Geen meting" en is nooit 0; lijn, tabel en voorleestekst
// zeggen hetzelfde.
import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HistoryChart, HISTORY_KEYS_HINT } from '../climate/HistoryChart';
import {
  HOUR_MS,
  extent,
  gapRuns,
  gapsBefore,
  hasValues,
  historyRange,
  historySlots,
  hourTicks,
  lastMeasured,
  lineRuns,
  momentLabel,
  slotLabel,
  slotReadout,
  staleSince,
} from '../climate/history';
import type { ZoneHistory } from '../api/types';

const TZ = 'Europe/Amsterdam'; // UTC+1 in november
const at = (hhmm: string) => `2026-11-09T${hhmm}:00Z`;

/** 08:00–12:59 UTC (09:00–13:59 in Amsterdam): metingen elk kwartier, behalve 10:00–10:59 UTC (geen meting). Doel 21,
 * om 11:30 UTC naar 19. */
function sample(): ZoneHistory {
  const temperature: [string, number | null][] = [];
  const target: [string, number | null][] = [];
  for (const h of [8, 9, 11, 12]) {
    for (const m of [0, 15, 30, 45]) {
      const ts = at(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
      temperature.push([ts, 19 + h / 10 + m / 600]);
      target.push([ts, h === 11 && m >= 30 ? 19 : h === 12 ? 19 : 21]);
    }
  }
  return { temperature, target };
}

describe('per uur samenvatten (history.ts)', () => {
  it('maakt één rij per klokuur, van het eerste tot het laatste punt, met het gat als uur zonder meting', () => {
    const slots = historySlots(sample());
    expect(slots.map((s) => slotLabel(s, TZ))).toEqual([
      '9 nov 09:00–10:00',
      '9 nov 10:00–11:00',
      '9 nov 11:00–12:00',
      '9 nov 12:00–13:00',
      '9 nov 13:00–13:45',
    ]);
    expect(slots[2].measured).toBeNull();
    expect(slots[2].target).toBeNull();
    expect(slots[0].measured).toMatchObject({ n: 4, min: 19.8, max: 19.875 });
    expect(slots[0].measured!.avg).toBeCloseTo(19.8375, 5);
    // Het doel verandert binnen het uur: beide waarden, in volgorde.
    expect(slots[3].target).toEqual([21, 19]);
    expect(slots[4].target).toEqual([19]);
  });

  it('telt een meting zonder waarde (null) nooit als 0 en breekt de lijn daar', () => {
    const h: ZoneHistory = {
      temperature: [[at('08:00'), 20], [at('08:10'), null], [at('08:20'), 21], [at('08:30'), 21.5]],
      target: [],
    };
    const [slot] = historySlots(h);
    expect(slot.measured).toMatchObject({ n: 3, min: 20, max: 21.5 });
    const runs = lineRuns(h.temperature, slot.start);
    expect(runs.map((r) => r.map((p) => p.v))).toEqual([[20], [21, 21.5]]);
  });

  it('breekt de lijn na elk uur zonder meting en arceert alleen uren zonder enige meting', () => {
    const h = sample();
    const slots = historySlots(h);
    const runs = lineRuns(h.temperature, slots[0].start);
    expect(runs).toHaveLength(2);
    expect(runs[0].at(-1)!.t).toBe(new Date(at('09:45')).getTime());
    expect(runs[1][0].t).toBe(new Date(at('11:00')).getTime());
    expect(gapRuns(slots)).toEqual([{ from: slots[2].start, to: slots[2].end }]);
    // Alleen het doel ontbreekt: geen arcering (de gemeten lijn loopt door).
    const alleenGemeten = historySlots({ temperature: [[at('08:00'), 20], [at('09:30'), 20], [at('10:30'), 20]], target: [[at('08:00'), 21]] });
    expect(gapRuns(alleenGemeten)).toEqual([]);
  });

  it('leest een uur voor met gemiddelde, laagste en hoogste meting en het doel; een gat is "Geen meting"', () => {
    const slots = historySlots(sample());
    expect(slotReadout(slots[0], TZ)).toBe('9 nov 09:00–10:00: gemeten gemiddeld 19,8° (laagst 19,8°, hoogst 19,9°); doel 21,0°');
    expect(slotReadout(slots[2], TZ)).toBe('9 nov 11:00–12:00: Geen meting');
    expect(slotReadout(slots[3], TZ)).toMatch(/; doel 21,0° en daarna 19,0°$/);
    // Eén punt: begin en eind vallen in dezelfde minuut, dus de tijd staat er één keer (niet "09:00–09:00").
    const alleenDoel = historySlots({ temperature: [], target: [[at('08:00'), 21]] });
    expect(slotReadout(alleenDoel[0], TZ)).toBe('9 nov 09:00: gemeten: Geen meting; doel 21,0°');
  });

  it('de laatste meting precies op het hele uur krijgt een eigen regel met één tijd', () => {
    const slots = historySlots({ temperature: [[at('08:30'), 20], [at('09:00'), 20.5]], target: [] });
    expect(slots.map((s) => slotLabel(s, TZ))).toEqual(['9 nov 09:30–10:00', '9 nov 10:00']);
    expect(slotReadout(slots[1], TZ)).toBe('9 nov 10:00: gemeten 20,5°; doel: Geen meting');
  });

  it('bepaalt bereik en tijdvak met een lus, ook bij heel veel metingen (geen limiet op argumenten)', () => {
    expect(extent([])).toBeNull();
    expect(extent([null, Number.NaN])).toBeNull();
    expect(extent([3, null, -2, 7.5])).toEqual({ min: -2, max: 7.5 });
    // Elke seconde een meting, 48 uur lang: 172 800 punten per reeks.
    const start = new Date(at('00:00')).getTime();
    const veel: [string, number | null][] = Array.from({ length: 172_800 }, (_, i) => [new Date(start + i * 1000).toISOString(), 20 + (i % 10) / 10]);
    const h: ZoneHistory = { temperature: veel, target: veel };
    expect(historyRange(h)).toEqual({ t0: start, t1: start + 172_799_000 });
    expect(historySlots(h)).toHaveLength(48);
    expect(extent(veel.map(([, v]) => v))).toEqual({ min: 20, max: 20.9 });
  });
});

describe('grafiek "Laatste 24 uur" met het toetsenbord', () => {
  it('houdt de toegankelijke naam, beschrijft de toetsen en is één focusstop', async () => {
    const user = userEvent.setup();
    render(<HistoryChart history={sample()} tz={TZ} />);
    const plot = screen.getByRole('img');
    expect(plot).toHaveAccessibleName('Gemeten en doeltemperatuur, bereik 18° tot 22°, laatste meting 20,3°');
    expect(plot).toHaveAccessibleDescription(HISTORY_KEYS_HINT);
    expect(plot).toHaveAttribute('tabindex', '0');
    // De uitleg is pas zichtbaar als de grafiek de focus heeft (en nog geen uur gekozen is).
    expect(screen.getByText(HISTORY_KEYS_HINT)).toHaveClass('sr-only');
    await user.tab();
    expect(plot).toHaveFocus();
    expect(screen.getByText(HISTORY_KEYS_HINT)).toHaveClass('hist-hint');
    expect(screen.getByTestId('hist-lezing')).toHaveTextContent(/^$/);
  });

  it('→ begint bij het eerste uur, ← bij het laatste; Home, End en Esc', async () => {
    const user = userEvent.setup();
    render(<HistoryChart history={sample()} tz={TZ} />);
    const lezing = screen.getByTestId('hist-lezing');
    expect(lezing).toHaveAttribute('aria-live', 'polite');
    await user.tab();
    await user.keyboard('{ArrowRight}');
    expect(lezing).toHaveTextContent(/^9 nov 09:00–10:00: gemeten gemiddeld 19,8°/);
    expect(screen.getByTestId('hist-kruis')).toBeInTheDocument();
    expect(screen.getByText(HISTORY_KEYS_HINT)).toHaveClass('sr-only');
    await user.keyboard('{ArrowRight}{ArrowRight}');
    expect(lezing).toHaveTextContent('9 nov 11:00–12:00: Geen meting');
    await user.keyboard('{End}');
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45: gemeten gemiddeld 20,2°/);
    await user.keyboard('{ArrowRight}'); // blijft op het laatste uur
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45/);
    await user.keyboard('{Home}');
    expect(lezing).toHaveTextContent(/^9 nov 09:00–10:00/);
    await user.keyboard('{ArrowLeft}'); // blijft op het eerste uur
    expect(lezing).toHaveTextContent(/^9 nov 09:00–10:00/);
    await user.keyboard('{Escape}');
    expect(lezing).toHaveTextContent(/^$/);
    expect(screen.queryByTestId('hist-kruis')).not.toBeInTheDocument();
    await user.keyboard('{ArrowLeft}');
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45/);
    // Focus weg: de keuze vervalt.
    await user.tab();
    expect(lezing).toHaveTextContent(/^$/);
  });

  it('laat andere toetsen door (Tab, Enter)', () => {
    render(<HistoryChart history={sample()} tz={TZ} />);
    const plot = screen.getByRole('img');
    act(() => plot.focus());
    expect(fireEvent.keyDown(plot, { key: 'Enter' })).toBe(true); // niet afgevangen
    expect(fireEvent.keyDown(plot, { key: 'Escape' })).toBe(true); // niets gekozen: Esc blijft vrij
    expect(fireEvent.keyDown(plot, { key: 'ArrowRight' })).toBe(false);
  });

  it('de aanwijzer kiest hetzelfde uur als het toetsenbord', () => {
    render(<HistoryChart history={sample()} tz={TZ} />);
    const plot = screen.getByRole('img');
    const area = plot.querySelector<HTMLElement>('.hist-area')!;
    area.getBoundingClientRect = () => ({ left: 100, width: 500, top: 0, height: 180, right: 600, bottom: 180, x: 100, y: 0, toJSON: () => ({}) });
    // jsdom kent PointerEvent niet: een MouseEvent met het type van de aanwijzer draagt clientX wel.
    const pointer = (type: string, clientX: number) => fireEvent(plot, new MouseEvent(type, { bubbles: true, clientX }));
    const lezing = screen.getByTestId('hist-lezing');
    // 08:00–12:45 UTC over 500 px: 50 % = 10:22 UTC = het uur 11:00–12:00 in Amsterdam (geen meting).
    pointer('pointermove', 350);
    expect(lezing).toHaveTextContent('9 nov 11:00–12:00: Geen meting');
    pointer('pointermove', 100);
    expect(lezing).toHaveTextContent(/^9 nov 09:00–10:00/);
    pointer('pointermove', 900); // buiten rechts: het laatste uur
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45/);
    // De muis verlaat het plotvak (zonder focus): de keuze vervalt.
    pointer('pointermove', 220);
    expect(lezing).toHaveTextContent(/^9 nov 10:00–11:00/);
    fireEvent.pointerLeave(plot);
    expect(lezing).toHaveTextContent(/^$/);
    // Zonder bruikbare positie gebeurt er niets (geen fout).
    fireEvent.pointerMove(plot);
    expect(lezing).toHaveTextContent(/^$/);
  });

  it('een tik (telefoon) kiest een uur en dat blijft staan als de vinger loslaat', () => {
    render(<HistoryChart history={sample()} tz={TZ} />);
    const plot = screen.getByRole('img');
    const area = plot.querySelector<HTMLElement>('.hist-area')!;
    area.getBoundingClientRect = () => ({ left: 100, width: 500, top: 0, height: 180, right: 600, bottom: 180, x: 100, y: 0, toJSON: () => ({}) });
    // Een vinger: pointerdown, en bij loslaten meteen pointerout/pointerleave (er is geen zweven), nog vóór de focus.
    const touch = (type: string, clientX: number) => {
      const e = new MouseEvent(type, { bubbles: true, clientX });
      Object.defineProperty(e, 'pointerType', { value: 'touch' });
      return fireEvent(plot, e);
    };
    const lezing = screen.getByTestId('hist-lezing');
    touch('pointerdown', 220);
    expect(lezing).toHaveTextContent(/^9 nov 10:00–11:00/);
    touch('pointerout', 220);
    expect(plot).not.toHaveFocus();
    expect(lezing).toHaveTextContent(/^9 nov 10:00–11:00/);
    expect(screen.getByTestId('hist-kruis')).toBeInTheDocument();
    // Een volgende tik kiest een ander uur; een tik ernaast (de grafiek had de focus en verliest die) wist de keuze.
    touch('pointerdown', 590);
    touch('pointerout', 590);
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45/);
    act(() => plot.focus());
    act(() => plot.blur());
    expect(lezing).toHaveTextContent(/^$/);
  });

  it('vegen met een vinger (de browser schuift de pagina: pointercancel) kiest geen uur; een getikt uur blijft staan', () => {
    render(<HistoryChart history={sample()} tz={TZ} />);
    const plot = screen.getByRole('img');
    const area = plot.querySelector<HTMLElement>('.hist-area')!;
    area.getBoundingClientRect = () => ({ left: 100, width: 500, top: 0, height: 180, right: 600, bottom: 180, x: 100, y: 0, toJSON: () => ({}) });
    const touch = (type: string, clientX: number) => {
      const e = new MouseEvent(type, { bubbles: true, clientX });
      Object.defineProperty(e, 'pointerType', { value: 'touch' });
      return fireEvent(plot, e);
    };
    const lezing = screen.getByTestId('hist-lezing');
    // Een veeg begint als een tik; neemt de browser het schuiven over, dan was het geen keuze.
    touch('pointerdown', 220);
    touch('pointercancel', 220);
    expect(lezing).toHaveTextContent(/^$/);
    expect(screen.queryByTestId('hist-kruis')).toBeNull();
    expect(plot).not.toHaveFocus();
    // Eerst een tik op het laatste uur, daarna een veeg die op een ander uur begint: het getikte uur blijft.
    touch('pointerdown', 590);
    touch('pointerup', 590);
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45/);
    touch('pointerdown', 220);
    touch('pointercancel', 220);
    expect(lezing).toHaveTextContent(/^9 nov 13:00–13:45/);
    expect(screen.getByTestId('hist-kruis')).toBeInTheDocument();
  });

  it('slepen met een vinger: na loslaten heeft het plotvak de focus, zodat een tik ernaast de keuze wist', () => {
    render(<HistoryChart history={sample()} tz={TZ} />);
    const plot = screen.getByRole('img');
    const area = plot.querySelector<HTMLElement>('.hist-area')!;
    area.getBoundingClientRect = () => ({ left: 100, width: 500, top: 0, height: 180, right: 600, bottom: 180, x: 100, y: 0, toJSON: () => ({}) });
    const touch = (type: string, clientX: number) => {
      const e = new MouseEvent(type, { bubbles: true, clientX });
      Object.defineProperty(e, 'pointerType', { value: 'touch' });
      return fireEvent(plot, e);
    };
    const lezing = screen.getByTestId('hist-lezing');
    // Slepen is geen tik: de browser stuurt dan geen click, en zonder focus kon een tik ernaast niets wissen.
    touch('pointerdown', 100);
    touch('pointermove', 220);
    touch('pointerup', 220);
    expect(lezing).toHaveTextContent(/^9 nov 10:00–11:00/);
    expect(plot).toHaveFocus();
    act(() => plot.blur());
    expect(lezing).toHaveTextContent(/^$/);
    expect(screen.queryByTestId('hist-kruis')).toBeNull();
  });

  it('houdt plaats vrij voor de langste uitlezing, onzichtbaar en niet voor de schermlezer', () => {
    const { container } = render(<HistoryChart history={sample()} tz={TZ} />);
    const maat = container.querySelector('.hist-maat')!;
    expect(maat).toHaveAttribute('aria-hidden', 'true');
    expect(maat).not.toHaveAttribute('aria-live');
    // Het uur waarin het doel verandert, heeft de langste zin.
    expect(maat).toHaveTextContent(/^9 nov 12:00–13:00: gemeten gemiddeld .*; doel 21,0° en daarna 19,0°$/);
    // Dezelfde plek als de echte uitlezing en de uitleg.
    expect(maat.parentElement).toBe(screen.getByTestId('hist-lezing').parentElement);
    expect(maat.parentElement).toHaveClass('hist-onder');
  });
});

describe('grafiek "Laatste 24 uur": eerlijk over gaten', () => {
  it('arceert een gat zonder meting (met het woord erin en een uitleg eronder); de lijn breekt', () => {
    const { container } = render(<HistoryChart history={sample()} tz={TZ} />);
    const gaten = screen.getAllByTestId('hist-gat');
    expect(gaten).toHaveLength(1);
    expect(gaten[0]).toHaveClass('arcering-gat');
    // Of het woord past, beslist de containerquery in de browser (e2e); hier staat het er.
    expect(gaten[0]).toHaveTextContent('Geen meting');
    expect(screen.getByText('Gearceerd: geen meting (nooit 0).')).toBeInTheDocument();
    const d = container.querySelectorAll('svg path')[1].getAttribute('d')!; // gemeten
    expect(d.match(/M/g)).toHaveLength(2);
  });

  it('zonder gat geen uitleg over arcering', () => {
    render(<HistoryChart history={{ temperature: [[at('08:00'), 20], [at('08:30'), 20.5]], target: [[at('08:00'), 21]] }} tz={TZ} />);
    expect(screen.queryByTestId('hist-gat')).not.toBeInTheDocument();
    expect(screen.queryByText(/Gearceerd/)).not.toBeInTheDocument();
  });

  it('een losse meting wordt een stip, niet onzichtbaar; de laatste meting is het eindpunt', () => {
    const { container } = render(<HistoryChart history={{ temperature: [[at('08:00'), 20], [at('11:00'), 21]], target: [] }} tz={TZ} />);
    expect(container.querySelectorAll('.hist-punt')).toHaveLength(1);
    expect(screen.getAllByTestId('hist-eind')).toHaveLength(1);
    expect(screen.getByTestId('hist-eind')).toHaveStyle({ left: '100.00%' });
    expect(container.querySelector('svg path[stroke="var(--chart-1)"]')).toBeNull();
  });

  it('alleen metingen zonder waarde: "Geen meting", nooit een lijn op 0', () => {
    render(<HistoryChart history={{ temperature: [[at('08:00'), null]], target: [] }} tz={TZ} />);
    expect(screen.getByText('Geen meting in de laatste 24 uur.')).toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('de laatste meting in de naam is de laatste mét waarde', () => {
    render(<HistoryChart history={{ temperature: [[at('08:00'), 20], [at('08:30'), 20.4], [at('08:40'), null]], target: [] }} tz={TZ} />);
    expect(screen.getByRole('img')).toHaveAccessibleName('Gemeten en doeltemperatuur, bereik 19° tot 21°, laatste meting 20,4°');
  });
});

describe('"Toon als tabel, per uur" (WCAG-tweeling)', () => {
  it('toont per uur gemiddelde, laagste en hoogste meting en het doel, met "Geen meting" in het gat', async () => {
    const user = userEvent.setup();
    render(<HistoryChart history={sample()} tz={TZ} />);
    const summary = screen.getByText('Toon als tabel, per uur');
    expect(summary.tagName).toBe('SUMMARY');
    await user.click(summary);
    const table = screen.getByRole('table', { name: 'Laatste 24 uur per uur: gemeten temperatuur en doeltemperatuur' });
    expect(within(table).getAllByRole('columnheader').map((c) => c.textContent)).toEqual(['Uur', 'Gemeten (gemiddeld)', 'Laagst – hoogst', 'Doel']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(5);
    expect(within(rows[0]).getByRole('rowheader')).toHaveTextContent('9 nov 09:00–10:00');
    expect(within(rows[0]).getAllByRole('cell').map((c) => c.textContent)).toEqual(['19,8°', '19,8° – 19,9°', '21,0°']);
    expect(within(rows[2]).getAllByRole('cell').map((c) => c.textContent)).toEqual(['Geen meting', 'Geen meting', 'Geen meting']);
    expect(within(rows[3]).getAllByRole('cell')[2]).toHaveTextContent('21,0° → 19,0°');
    // Op een telefoon wordt elke rij een kaart: elke cel draagt zijn kolomnaam.
    expect(within(rows[0]).getAllByRole('cell').map((c) => c.getAttribute('data-label'))).toEqual(['Gemeten (gemiddeld)', 'Laagst – hoogst', 'Doel']);
  });

  it('dezelfde uren als het toetsenbord', async () => {
    const user = userEvent.setup();
    render(<HistoryChart history={sample()} tz={TZ} />);
    await user.click(screen.getByText('Toon als tabel, per uur'));
    const rowHeaders = screen.getAllByRole('rowheader').map((r) => r.textContent);
    await user.click(screen.getByRole('img'));
    await user.keyboard('{Home}');
    for (const header of rowHeaders) {
      expect(screen.getByTestId('hist-lezing').textContent!.startsWith(`${header}: `)).toBe(true);
      await user.keyboard('{ArrowRight}');
    }
  });
});


const ms = (hhmm: string) => new Date(at(hhmm)).getTime();
const vers = { stale: false, at: at('12:45'), value: 20.275 };
const verouderd = { stale: true, at: at('12:45'), value: 20.275 };

describe('verouderd, eindpunt en tijdas (history.ts)', () => {
  it('lastMeasured geeft de laatste meting mét waarde, of null', () => {
    expect(lastMeasured({ temperature: [[at('08:00'), 20], [at('08:30'), 20.4], [at('08:40'), null]], target: [] })).toEqual({ t: ms('08:30'), v: 20.4 });
    expect(lastMeasured({ temperature: [[at('08:00'), null]], target: [[at('08:00'), 21]] })).toBeNull();
  });

  it('hasValues: alleen met een gemeten waarde of een doel is er een grafiek', () => {
    expect(hasValues({ temperature: [], target: [] })).toBe(false);
    expect(hasValues({ temperature: [[at('08:00'), null]], target: [] })).toBe(false);
    expect(hasValues({ temperature: [], target: [[at('08:00'), 21]] })).toBe(true);
  });

  it('staleSince: alleen als de server "verouderd" zegt; het moment is de laatste meting', () => {
    const h = sample();
    expect(staleSince(h, vers, ms('16:00'), 1800)).toBeNull();
    expect(staleSince(h, null, ms('16:00'), 1800)).toBeNull();
    expect(staleSince(h, undefined, ms('16:00'), 1800)).toBeNull();
    const oud = staleSince(h, verouderd, ms('16:00'), 1800)!;
    expect(oud.sinds).toBe(ms('12:45'));
    expect(oud.waarde).toBeCloseTo(20.275, 5);
    expect(oud.tot).toBe(ms('16:00'));
  });

  it('staleSince: de klok van het apparaat bepaalt alleen de breedte, binnen grenzen van de server', () => {
    const h = sample();
    // Klok loopt achter (vóór de laatste meting): minstens de termijn waarna de server "verouderd" zegt.
    expect(staleSince(h, verouderd, ms('09:00'), 1800)!.tot).toBe(ms('12:45') + 1800_000);
    // Klok loopt ver voor: hoogstens 24 uur na de laatste meting in de grafiek.
    expect(staleSince(h, verouderd, ms('12:45') + 72 * HOUR_MS, 1800)!.tot).toBe(ms('12:45') + 24 * HOUR_MS);
    // Geen bruikbare klok of termijn: het stuk begint en eindigt bij de laatste meting (geen verzonnen breedte).
    expect(staleSince(h, verouderd, NaN)!.tot).toBe(ms('12:45'));
  });

  it('staleSince: zonder meting in de grafiek telt de laatste meting van de server; zonder beide is er niets te melden', () => {
    const alleenDoel: ZoneHistory = { temperature: [], target: [[at('08:00'), 21]] };
    expect(staleSince(alleenDoel, { stale: true, at: '2026-11-07T03:27:00Z', value: 19.5 }, ms('16:00'), 1800)).toEqual({
      sinds: new Date('2026-11-07T03:27:00Z').getTime(),
      waarde: 19.5,
    });
    expect(staleSince(alleenDoel, { stale: true, at: null, value: null }, ms('16:00'), 1800)).toBeNull();
    // Een tijd zonder waarde is geen meting.
    expect(staleSince(alleenDoel, { stale: true, at: at('07:00'), value: null }, ms('16:00'), 1800)).toBeNull();
  });

  it('historyRange en historySlots lopen door tot "tot": de uren na de laatste meting hebben geen meting', () => {
    const h = sample();
    expect(historyRange(h, ms('16:00'))).toEqual({ t0: ms('08:00'), t1: ms('16:00') });
    // Nooit korter dan de metingen zelf.
    expect(historyRange(h, ms('09:00'))).toEqual({ t0: ms('08:00'), t1: ms('12:45') });
    const slots = historySlots(h, ms('15:30'));
    expect(slots).toHaveLength(8);
    expect(slots.slice(5).every((s) => s.measured === null && s.target === null)).toBe(true);
    expect(slotLabel(slots[7], TZ)).toBe('9 nov 16:00–16:30');
  });

  it('gapsBefore: gaten in het verouderde stuk vervallen; een gat eroverheen stopt bij de grens', () => {
    const gaten = [{ from: 10, to: 20 }, { from: 30, to: 50 }, { from: 60, to: 70 }];
    expect(gapsBefore(gaten, null)).toEqual(gaten);
    expect(gapsBefore(gaten, 40)).toEqual([{ from: 10, to: 20 }, { from: 30, to: 40 }]);
    expect(gapsBefore(gaten, 30)).toEqual([{ from: 10, to: 20 }]);
  });

  it('hourTicks: hele klokuren op een vaste stap, niet vlak bij begin of eind', () => {
    const uren = (t0: string, t1: string) => hourTicks(new Date(t0).getTime(), new Date(t1).getTime(), TZ).map((t) => new Date(t).toISOString().slice(11, 16));
    // 24 uur: elke 3 uur op de klok van het huis (Amsterdam = UTC+1): 09, 12, 15, 18, 21, 00, 03 uur lokaal.
    expect(uren('2026-11-08T06:37:00Z', '2026-11-09T06:37:00Z')).toEqual(['11:00', '14:00', '17:00', '20:00', '23:00', '02:00']);
    // Korter dan twee uur: geen tussenlabels.
    expect(uren('2026-11-09T08:00:00Z', '2026-11-09T09:30:00Z')).toEqual([]);
    // Zes uur: elk uur, zonder de uren binnen 15 % van begin en eind.
    expect(uren('2026-11-09T08:00:00Z', '2026-11-09T14:00:00Z')).toEqual(['09:00', '10:00', '11:00', '12:00', '13:00']);
    expect(hourTicks(NaN, NaN, TZ)).toEqual([]);
  });

  it('momentLabel: dag en tijd in de tijdzone van het huis', () => {
    expect(momentLabel(ms('12:45'), TZ)).toBe('9 nov 13:45');
  });
});

describe('grafiek "Laatste 24 uur": verouderde sensor (Peil §10)', () => {
  const NU = ms('16:00');

  it('verse sensor: geen verouderd-stuk, de naam blijft gelijk, bron en tijd staan onder de grafiek', () => {
    render(<HistoryChart history={sample()} tz={TZ} sensor={vers} staleAfterS={1800} bron="sensor van de zone" now={NU} />);
    expect(screen.queryByTestId('hist-oud')).not.toBeInTheDocument();
    expect(screen.queryByTestId('hist-verouderd')).not.toBeInTheDocument();
    expect(screen.queryByText(/Verouderd/)).not.toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAccessibleName('Gemeten en doeltemperatuur, bereik 18° tot 22°, laatste meting 20,3°');
    // Er is een gearceerd gat: de uitleg van de arcering staat er, de zin over "een gat" dus niet nog eens.
    expect(screen.getByTestId('hist-bron')).toHaveTextContent('Bron: sensor van de zone · bijgewerkt 9 nov 13:45.');
    expect(screen.getByTestId('hist-bron').querySelector('time')).toHaveAttribute('datetime', '2026-11-09T12:45:00.000Z');
    // De tijdas eindigt bij de laatste meting.
    expect(screen.getByTestId('hist-eind')).toHaveStyle({ left: '100.00%' });
  });

  it('zonder gat staat onder de grafiek wat een gat betekent (nooit 0)', () => {
    render(<HistoryChart history={{ temperature: [[at('08:00'), 20], [at('08:30'), 20.5]], target: [[at('08:00'), 21]] }} tz={TZ} bron="sensor van de airco" />);
    expect(screen.getByTestId('hist-bron')).toHaveTextContent('Bron: sensor van de airco · bijgewerkt 9 nov 09:30 · een gat betekent dat er geen meting was (nooit 0).');
  });

  it('de grafiek is een figuur met een bijschrift; de tabel staat erbuiten', () => {
    const { container } = render(<HistoryChart history={sample()} tz={TZ} />);
    const figuur = screen.getByRole('figure');
    expect(figuur).toContainElement(screen.getByRole('img'));
    expect(figuur.lastElementChild!.tagName).toBe('FIGCAPTION');
    expect(figuur).toContainElement(screen.getByTestId('hist-lezing'));
    expect(figuur).not.toContainElement(container.querySelector('details'));
    // Zonder opgegeven bron heet de bron wat zij is: de temperatuursensor.
    expect(screen.getByTestId('hist-bron')).toHaveTextContent('Bron: temperatuursensor · bijgewerkt 9 nov 13:45.');
  });

  it('verouderd: de lijn stopt bij de laatste meting, daarna een gearceerd stuk "Verouderd sinds …" tot nu', () => {
    const { container } = render(<HistoryChart history={sample()} tz={TZ} sensor={verouderd} staleAfterS={1800} bron="sensor van de zone" now={NU} />);
    // 08:00–16:00 UTC is acht uur; de laatste meting (12:45) ligt op 59,38 %.
    const stuk = screen.getByTestId('hist-oud');
    expect(stuk).toHaveClass('arcering-gat', 'hist-oud-rand');
    expect(stuk).toHaveStyle({ left: '59.38%' });
    expect(screen.getByTestId('hist-eind')).toHaveStyle({ left: '59.38%' });
    expect(container.querySelector('.hist-oudlabel')).toHaveTextContent('Verouderd sinds 13:45');
    expect(container.querySelector('.hist-oudlabel')).toHaveStyle({ left: '59.38%' });
    // Geen direct label bij het eindpunt: de laatst bekende waarde staat onder de grafiek.
    expect(container.querySelector('.hist-eindlabel')).toBeNull();
    expect(container.querySelector('.hist-eindmaat')).toBeNull();
    expect(container.querySelector('.hist-chart')).not.toHaveAttribute('data-eindlabel');
    // De tijdas loopt door tot nu (17:00 in Amsterdam).
    expect([...container.querySelectorAll('.hist-x-in span:not(.hist-x-tussen)')].map((e) => e.textContent)).toEqual(['09:00', '17:00']);
    // Het gat van 11:00–12:00 blijft "Geen meting"; in het verouderde stuk staat geen tweede arcering.
    expect(screen.getAllByTestId('hist-gat')).toHaveLength(1);
    // De gemeten lijn loopt niet door na de laatste meting (x hoogstens 593,8 van 1000).
    const d = container.querySelector('svg path[stroke="var(--chart-1)"]')!.getAttribute('d')!;
    const xs = [...d.matchAll(/[ML](\d+(?:\.\d+)?) /g)].map((m) => Number(m[1]));
    expect(Math.max(...xs)).toBeCloseTo(593.8, 1);
    // Ook de doellijn stopt daar: zij loopt het gearceerde stuk niet in (niet door tot het eind van het uur, 625).
    const doel = container.querySelector('svg path[stroke="var(--chart-limit)"]')!.getAttribute('d')!;
    expect(doel.endsWith(' H593.8')).toBe(true);
  });

  it('verouderd met een doel dat nog wel binnenkomt: de doellijn loopt tot haar eigen laatste waarde, niet verder', () => {
    // De sensor van de zone zwijgt sinds 12:45; de airco meldt het doel nog tot 14:20.
    const h = sample();
    h.target.push([at('13:30'), 19], [at('14:20'), 19]);
    const { container } = render(<HistoryChart history={h} tz={TZ} sensor={verouderd} staleAfterS={1800} now={NU} />);
    expect(screen.getByTestId('hist-oud')).toHaveStyle({ left: '59.38%' });
    // 14:20 ligt op 6 u 20 van de 8 uur = 791,7; het eind van dat uur (15:00 = 875) haalt de lijn niet.
    const doel = container.querySelector('svg path[stroke="var(--chart-limit)"]')!.getAttribute('d')!;
    expect(doel.endsWith(' H791.7')).toBe(true);
  });

  it('verouderd: naam, pil en bijschrift zeggen het in woorden (nooit alleen kleur, nooit groen)', () => {
    render(<HistoryChart history={sample()} tz={TZ} sensor={verouderd} staleAfterS={1800} bron="sensor van de zone" now={NU} />);
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'Gemeten en doeltemperatuur, bereik 18° tot 22°, laatste meting 20,3°, verouderd sinds 9 nov 13:45',
    );
    const regel = screen.getByTestId('hist-verouderd');
    expect(regel).toHaveTextContent('Verouderd');
    expect(regel).toHaveTextContent('Laatst bekend 9 nov 13:45: 20,3°. Daarna kwam er geen meting meer.');
    const pil = regel.querySelector('.status-badge')!;
    expect(pil).toHaveAttribute('data-status', 'stale');
    expect(pil).toHaveClass('arcering', 'border-dashed', 'border-stale');
    expect(pil.className).not.toMatch(/healthy/);
    expect(regel.querySelector('time')).toHaveAttribute('datetime', '2026-11-09T12:45:00.000Z');
    expect(screen.getByText('Gearceerd: geen meting (nooit 0).')).toBeInTheDocument();
    // De tijd staat al in de regel "Laatst bekend": de bronregel herhaalt haar niet.
    expect(screen.getByTestId('hist-bron')).toHaveTextContent('Bron: sensor van de zone.');
    expect(screen.getByTestId('hist-bron')).not.toHaveTextContent('bijgewerkt');
  });

  it('verouderd: de uren na de laatste meting heten "Geen meting" in de uitlezing en in de tabel', async () => {
    const user = userEvent.setup();
    render(<HistoryChart history={sample()} tz={TZ} sensor={verouderd} staleAfterS={1800} now={NU} />);
    await user.click(screen.getByRole('img'));
    await user.keyboard('{End}');
    expect(screen.getByTestId('hist-lezing')).toHaveTextContent('9 nov 17:00: Geen meting');
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByTestId('hist-lezing')).toHaveTextContent('9 nov 16:00–17:00: Geen meting');
    await user.click(screen.getByText('Toon als tabel, per uur'));
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows).toHaveLength(9);
    expect(within(rows[5]).getAllByRole('cell').map((c) => c.textContent)).toEqual(['Geen meting', 'Geen meting', 'Geen meting']);
    expect(within(rows[4]).getAllByRole('cell')[0]).toHaveTextContent('20,2°');
  });

  it('verouderd sinds vóór het begin van de grafiek: het hele vak is gearceerd, met de dag erbij en zonder rand', () => {
    const { container } = render(
      <HistoryChart history={{ temperature: [], target: [[at('08:00'), 21], [at('12:00'), 21]] }} tz={TZ} sensor={{ stale: true, at: '2026-11-07T03:27:00Z', value: 19.5 }} staleAfterS={1800} now={NU} />,
    );
    const stuk = screen.getByTestId('hist-oud');
    expect(stuk).toHaveStyle({ left: '0.00%' });
    expect(stuk).not.toHaveClass('hist-oud-rand');
    expect(container.querySelector('.hist-oudlabel')).toHaveTextContent('Verouderd sinds 7 nov 04:27');
    expect(screen.getByRole('img')).toHaveAccessibleName('Gemeten en doeltemperatuur, bereik 20° tot 22°, verouderd sinds 7 nov 04:27');
    expect(screen.getByTestId('hist-verouderd')).toHaveTextContent('Laatst bekend 7 nov 04:27: 19,5°. Daarna kwam er geen meting meer.');
    expect(screen.queryByTestId('hist-eind')).not.toBeInTheDocument();
  });

  it('verouderd zonder enige bekende meting: geen verzonnen tijd, wel eerlijk "geen gemeten temperatuur"', () => {
    render(<HistoryChart history={{ temperature: [], target: [[at('08:00'), 21], [at('08:30'), 21]] }} tz={TZ} sensor={{ stale: true, at: null, value: null }} now={NU} />);
    expect(screen.queryByTestId('hist-oud')).not.toBeInTheDocument();
    expect(screen.queryByTestId('hist-verouderd')).not.toBeInTheDocument();
    expect(screen.getByTestId('hist-bron')).toHaveTextContent(
      'Bron: temperatuursensor · geen gemeten temperatuur in de laatste 24 uur · een gat betekent dat er geen meting was (nooit 0).',
    );
  });

  it('direct label bij het eindpunt alleen bij een verse sensor; één tijd op de as als begin en eind gelijk zijn', () => {
    const { container } = render(<HistoryChart history={{ temperature: [[at('08:00'), 20.3]], target: [] }} tz={TZ} />);
    expect(container.querySelector('.hist-chart')).toHaveAttribute('data-eindlabel');
    expect(container.querySelector('.hist-eindlabel')).toHaveTextContent('20,3°');
    // De strook naast het tekenvlak en naast de tijden volgt de breedte van het label: een onzichtbare maat met
    // dezelfde tekst en dezelfde cijfers, buiten de toegankelijkheidsboom (in elk systeemfont past het label dan).
    const maten = [...container.querySelectorAll('.hist-eindmaat')];
    expect(maten.map((e) => e.parentElement!.className.split(' ')[0])).toEqual(['hist-plot', 'hist-x']);
    for (const m of maten) {
      expect(m).toHaveTextContent('20,3°');
      expect(m).toHaveClass('cijfers');
      expect(m).toHaveAttribute('aria-hidden', 'true');
    }
    expect(container.querySelectorAll('.hist-punt')).toHaveLength(0);
    expect([...container.querySelectorAll('.hist-x-in span')].map((e) => e.textContent)).toEqual(['09:00']);
  });
});
