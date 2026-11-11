// Grafiek "Laatste 24 uur": gemeten temperatuur (lijn, chart-1) en doel (traplijn, chart-limit), met een tabel per uur
// en bediening met toetsenbord en aanwijzer (besluit van de gebruiker, inventaris C09; Peil 1.1.1 §10; ADR-0013).
// Alleen weergave en toegankelijkheid: de API, de metingen en de bestaande toegankelijke naam blijven gelijk.
//  - plotvak: tabindex=0, role=img; ← en → per uur, Home/End eerste en laatste uur, Esc stopt; de aanwijzer (ook een
//    tik op een telefoon) kiest hetzelfde uur. Draadkruis = het gekozen uur als band; de waarde staat in één zichtbare
//    regel onder de grafiek, die ook wordt voorgelezen (aria-live="polite"). Een waarde is nooit alleen met de muis
//    te lezen, en nooit alleen met kleur. Met een muis vervalt de keuze als de aanwijzer het plotvak verlaat; na een
//    tik blijft zij staan (een vinger "verlaat" het vak meteen) tot een tik ernaast. Voor de uitlezing is bij een
//    muis vooraf ruimte gemaakt, zodat de tabel eronder niet verspringt tijdens het aanwijzen.
//  - eerlijkheid: een uur zonder meting breekt de lijn en heet "Geen meting" (nooit 0, nooit doorgetrokken); een
//    stuk zonder enige meting is gearceerd (arcering-gat), met het woord erin als het past (anders weg, nooit half:
//    containerquery in componenten.css) en dan een uitleg onder de grafiek.
//  - "Toon als tabel, per uur": dezelfde uren met gemiddelde, laagste en hoogste meting en het doel.
//  - verouderd (ontwerpreview, Peil §10, ADR-0014): zegt de server dat de sensor verouderd is, dan stopt de lijn bij de
//    laatste meting en volgt een gearceerd stuk met stippelrand en "Verouderd sinds …" tot nu; onder de grafiek staat
//    de pil "Verouderd" (icoon + woord + vorm, nooit groen) met de laatst bekende waarde. De uren in dat stuk heten
//    in de tabel en de uitlezing "Geen meting".
//  - figuur: <figure> met <figcaption> (bron, bijgewerkt, wat een gat betekent); de laatste meting is een eindpunt
//    van 12 px met een ring in de kaartkleur, op brede schermen met de waarde ernaast.
import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { ZoneHistory } from '../api/types';
import { Empty } from '../components/bits';
import { StatusBadge } from '../components/peil/StatusBadge';
import { temp, time } from '../format';
import {
  GEEN_METING,
  HOUR_MS,
  extent,
  gapRuns,
  gapsBefore,
  historyRange,
  historySlots,
  hourTicks,
  lastMeasured,
  lineRuns,
  measuredText,
  momentLabel,
  rangeText,
  slotIndex,
  slotLabel,
  slotReadout,
  staleSince,
  targetText,
  type HistorySlot,
  type SensorStand,
} from './history';

const W = 1000;
const H = 180;

export const HISTORY_KEYS_HINT =
  'Gebruik de pijltjes naar links en rechts om per uur te lezen, Home en End voor het eerste en het laatste uur en Esc om te stoppen. De tabel onder de grafiek bevat dezelfde gegevens.';

export interface HistoryChartProps {
  history: ZoneHistory;
  tz?: string;
  /** Het oordeel van de server over de sensor (`zone.temperature`): bij `stale` krijgt de grafiek "Verouderd sinds …". */
  sensor?: SensorStand | null;
  /** De termijn waarna de server de sensor verouderd noemt (`zone.policy.stale_after_s`). */
  staleAfterS?: number;
  /** Waar de metingen vandaan komen, voor onder de grafiek ("sensor van de zone"). */
  bron?: string;
  /** "Nu" in ms (alleen voor tests); standaard de klok van dit apparaat. */
  now?: number;
}

export function HistoryChart({ history, tz, sensor, staleAfterS, bron = 'temperatuursensor', now }: HistoryChartProps) {
  const hintId = useId();
  const area = useRef<HTMLDivElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  // Het gekozen uur op zijn begintijd (niet op de plaats in de lijst): bij verversen schuift het venster op.
  const [activeStart, setActiveStart] = useState<number | null>(null);
  const [focused, setFocused] = useState(false);

  const laatste = lastMeasured(history);
  const oud = staleSince(history, sensor, now ?? Date.now(), staleAfterS);
  const range = historyRange(history, oud?.tot);
  const bereik = extent([...history.temperature, ...history.target].map(([, v]) => v));
  if (!range) return <Empty>Nog geen geschiedenis.</Empty>;
  if (!bereik) return <Empty>{GEEN_METING} in de laatste 24 uur.</Empty>;

  const { t0, t1 } = range;
  const slots = historySlots(history, oud?.tot);
  const slot0 = slots[0].start;
  const v0 = Math.floor(bereik.min - 0.5), v1 = Math.ceil(bereik.max + 0.5);
  const x = (t: number) => ((t - t0) / (t1 - t0)) * W;
  const y = (v: number) => H - ((v - v0) / (v1 - v0)) * (H - 10) - 5;
  const pctX = (t: number) => `${(((t - t0) / (t1 - t0)) * 100).toFixed(2)}%`;
  const pctY = (v: number) => `${((y(v) / H) * 100).toFixed(2)}%`;

  // Gemeten: één doorgetrokken stuk per reeks metingen zonder gat; een los punt wordt een stip.
  const measuredRuns = lineRuns(history.temperature, slot0);
  const line = measuredRuns
    .filter((r) => r.length > 1)
    .map((r) => r.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' '))
    .join(' ');
  // De laatste meting is het eindpunt (12 px, Peil §10) en wordt dus niet ook nog als losse stip getekend.
  const dots = measuredRuns.filter((r) => r.length === 1 && r[0].t !== laatste?.t).map((r) => r[0]);
  // Verouderd: het stuk vanaf de laatste meting (of vanaf het begin, als die vóór de grafiek ligt) tot het eind.
  const oudVan = oud ? Math.max(oud.sinds, t0) : null;
  // Doel: traplijn; het laatste doel van een stuk loopt door tot het eind van zijn uur (of het eind van de grafiek).
  // Bij een verouderde sensor loopt het doel niet verder het gearceerde stuk in dan zijn eigen laatste waarde: ook
  // deze lijn stopt bij het laatste punt (Peil §10).
  const step = lineRuns(history.target, slot0)
    .map((r) => {
      const last = r[r.length - 1];
      const uurEind = Math.min(t1, slot0 + (slotIndex(last.t, slot0) + 1) * HOUR_MS);
      const until = oudVan === null ? uurEind : Math.min(uurEind, Math.max(last.t, oudVan));
      return r.map((p, i) => (i ? `H${x(p.t).toFixed(1)} V${y(p.v).toFixed(1)}` : `M${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`)).join(' ') + ` H${x(until).toFixed(1)}`;
    })
    .join(' ');
  const gaps = gapsBefore(gapRuns(slots), oudVan);
  const oudSinds = oud ? (oud.sinds >= t0 ? time(new Date(oud.sinds), tz) : momentLabel(oud.sinds, tz)) : '';
  const gearceerd = gaps.length > 0 || oudVan !== null;

  // Tijden onder de grafiek: begin links, eind rechts; tussenlabels op hele uren (alleen als er breedte voor is).
  const begin = time(new Date(t0), tz);
  const eind = time(new Date(t1), tz);
  const tussen = hourTicks(t0, t1, tz);
  // Review stap 5 (Peil §10, dataviz): rasterlijnen op ronde graden (stap 1 of 2) met het getal ernaast, en begin- en
  // eindtijd onder de grafiek; tekst in teksttokens (chart-label), nooit de reekskleur.
  const stap = v1 - v0 > 6 ? 2 : 1;
  const ticks: number[] = [];
  for (let v = Math.ceil(v0 / stap) * stap; v <= v1; v += stap) ticks.push(v);

  // Het label bij het eindpunt staat in een eigen strook rechts van het tekenvlak. Die strook is precies zo breed als
  // het label: een onzichtbare maat met dezelfde tekst en dezelfde letter houdt de plaats vrij, naast het tekenvlak
  // en naast de tijden (zodat de tijdas even breed blijft als het tekenvlak). Een vaste breedte paste maar bij één
  // systeemfont; in een breder font stak het label buiten het plotvak (.hist-eindmaat in componenten.css).
  const eindmaat = laatste && !oud ? <span className="hist-eindmaat cijfers" aria-hidden="true">{temp(laatste.v)}</span> : null;

  const activeIdx = activeStart === null ? -1 : slots.findIndex((s) => s.start === activeStart);
  const active: HistorySlot | null = activeIdx >= 0 ? slots[activeIdx] : null;
  const choose = (i: number | null) => setActiveStart(i === null ? null : slots[i].start);
  // De langste uitlezing van deze grafiek: onzichtbaar op de plaats van de uitlezing, zodat die plaats al zo hoog is
  // (alleen bij een muis, zie .hist-maat in componenten.css).
  const langste = slots.reduce((l, s) => {
    const r = slotReadout(s, tz);
    return r.length > l.length ? r : l;
  }, '');

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const lastIdx = slots.length - 1;
    let next: number | null | undefined;
    if (e.key === 'ArrowRight') next = activeIdx < 0 ? 0 : Math.min(lastIdx, activeIdx + 1);
    else if (e.key === 'ArrowLeft') next = activeIdx < 0 ? lastIdx : Math.max(0, activeIdx - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = lastIdx;
    else if (e.key === 'Escape' && activeIdx >= 0) next = null;
    if (next === undefined) return;
    e.preventDefault();
    choose(next);
  };
  const onPointer = (e: PointerEvent<HTMLDivElement>) => {
    const box = area.current?.getBoundingClientRect();
    if (!box || box.width <= 0 || !Number.isFinite(e.clientX)) return;
    const ratio = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    choose(Math.min(slots.length - 1, Math.max(0, slotIndex(t0 + ratio * (t1 - t0), slot0))));
  };

  return (
    <div className="hist-chart" data-eindlabel={laatste && !oud ? '' : undefined}>
      <figure className="hist-figuur">
        {/* Peil §10: plotvak role=img + tabindex=0. De toegankelijke naam is de samenvatting (bij een verouderde sensor
            met "verouderd sinds …" erachter); de uitleg van de toetsen is de beschrijving; de waarde per uur staat in de
            aria-live-regel eronder. */}
        <div
          ref={plot}
          className="hist-plot"
          role="img"
          tabIndex={0}
          aria-label={`Gemeten en doeltemperatuur, bereik ${v0}° tot ${v1}°${laatste ? `, laatste meting ${temp(laatste.v)}` : ''}${oud ? `, verouderd sinds ${momentLabel(oud.sinds, tz)}` : ''}`}
          aria-describedby={hintId}
          data-testid="hist-plot"
          onKeyDown={onKey}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false);
            choose(null);
          }}
          onPointerMove={onPointer}
          onPointerDown={onPointer}
          onPointerLeave={(e) => {
            // Een vinger verlaat het vak zodra hij loslaat: dan blijft de keuze staan (tot een tik ernaast, of Esc).
            if (e.pointerType !== 'touch' && document.activeElement !== plot.current) choose(null);
          }}
        >
          <div className="hist-y" aria-hidden="true">
            {ticks.map((v) => <span key={v} className="hist-tick cijfers" style={{ top: pctY(v) }}>{`${v}°`}</span>)}
          </div>
          <div className="hist-area" ref={area} aria-hidden="true">
            {gaps.map((g) => (
              <div key={g.from} className="hist-gat arcering-gat" style={{ left: pctX(g.from), width: `calc(${pctX(g.to)} - ${pctX(g.from)})` }} data-testid="hist-gat">
                <span>{GEEN_METING}</span>
              </div>
            ))}
            {oudVan !== null && <div className={`hist-oud arcering-gat${oud!.sinds >= t0 ? ' hist-oud-rand' : ''}`} style={{ left: pctX(oudVan) }} data-testid="hist-oud" />}
            {active && <div className="hist-kruis" style={{ left: pctX(active.from), width: `calc(${pctX(active.to)} - ${pctX(active.from)})` }} data-testid="hist-kruis" />}
            <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }} preserveAspectRatio="none" focusable="false">
              {/* Peil §7: rasterlijnen chart-grid; gemeten = chart-1 (2 px); doel = grenslijn in inkt (chart-limit),
                  gestreepte traplijn — geen "verwarmen"-kleur, want het doel is geen reeks verwarmen. */}
              {ticks.map((v) => <line key={v} x1="0" x2={W} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" vectorEffect="non-scaling-stroke" />)}
              {step && <path d={step} fill="none" stroke="var(--chart-limit)" strokeWidth="1.5" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />}
              {line && <path d={line} fill="none" stroke="var(--chart-1)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
            </svg>
            {dots.map((p) => <span key={p.t} className="hist-punt" style={{ left: pctX(p.t), top: pctY(p.v) }} />)}
            {laatste && <span className="hist-eind" style={{ left: pctX(laatste.t), top: pctY(laatste.v) }} data-testid="hist-eind" />}
            {/* Direct label bij het eindpunt: alleen bij een verse sensor en alleen als er breedte voor is; de strook
                ernaast volgt de breedte van dit label (eindmaat hierboven, componenten.css). */}
            {laatste && !oud && <span className="hist-eindlabel cijfers" style={{ left: pctX(laatste.t), top: pctY(laatste.v) }}>{temp(laatste.v)}</span>}
            {oudVan !== null && (
              <div className="hist-oudlabel" style={{ left: pctX(oudVan) }}>
                <span>Verouderd sinds {oudSinds}</span>
              </div>
            )}
          </div>
          {eindmaat}
        </div>
        <div className="hist-x cijfers" aria-hidden="true">
          <div className="hist-x-in">
            <span style={{ left: 0 }}>{begin}</span>
            {tussen.map((t) => <span key={t} className="hist-x-tussen" style={{ left: pctX(t) }}>{time(new Date(t), tz)}</span>)}
            {eind !== begin && <span style={{ right: 0 }}>{eind}</span>}
          </div>
          {eindmaat}
        </div>
        <div className="hist-onder">
          <p className="hist-lezing hist-maat cijfers" aria-hidden="true">{langste}</p>
          <p className="hist-lezing cijfers" aria-live="polite" data-testid="hist-lezing">
            {active ? slotReadout(active, tz) : ''}
          </p>
          <p id={hintId} className={focused && !active ? 'hist-hint' : 'sr-only'}>
            {HISTORY_KEYS_HINT}
          </p>
        </div>
        {/* Peil §10: figcaption met bron en tijd. Verouderd = pil (icoon + woord + vorm) met de laatst bekende waarde. */}
        <figcaption className="hist-bron">
          {oud && (
            <p className="hist-oudregel" data-testid="hist-verouderd">
              <StatusBadge status="stale" />
              <span>
                Laatst bekend <time dateTime={new Date(oud.sinds).toISOString()}>{momentLabel(oud.sinds, tz)}</time>
                {oud.waarde !== null ? `: ${temp(oud.waarde)}` : ''}. Daarna kwam er geen meting meer.
              </span>
            </p>
          )}
          {gearceerd && (
            <p className="hist-uitleg">
              <span className="hist-staal arcering-gat" aria-hidden="true" />
              Gearceerd: {GEEN_METING.toLowerCase()} (nooit 0).
            </p>
          )}
          <p className="hist-bronregel" data-testid="hist-bron">
            Bron: {bron}
            {!oud && laatste && <> · bijgewerkt <time dateTime={new Date(laatste.t).toISOString()}>{momentLabel(laatste.t, tz)}</time></>}
            {!oud && !laatste && ' · geen gemeten temperatuur in de laatste 24 uur'}
            {!gearceerd && ' · een gat betekent dat er geen meting was (nooit 0)'}.
          </p>
        </figcaption>
      </figure>
      <details className="hist-tabel">
        <summary>Toon als tabel, per uur</summary>
        <HistoryTable slots={slots} tz={tz} />
      </details>
    </div>
  );
}

/** De WCAG-tweeling van de grafiek: per uur het gemiddelde, de laagste en hoogste meting en het doel. */
export function HistoryTable({ slots, tz }: { slots: HistorySlot[]; tz?: string }) {
  return (
    <div className="table-wrap">
      <table className="stack-mobile hist-table">
        <caption className="sr-only">Laatste 24 uur per uur: gemeten temperatuur en doeltemperatuur</caption>
        <thead>
          <tr>
            <th scope="col">Uur</th>
            <th scope="col">Gemeten (gemiddeld)</th>
            <th scope="col">Laagst – hoogst</th>
            <th scope="col">Doel</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((s) => (
            <tr key={s.start}>
              <th scope="row" data-label="Uur" className="cijfers">{slotLabel(s, tz)}</th>
              <td data-label="Gemeten (gemiddeld)" className={s.measured ? 'cijfers' : 'hist-geen'}>{measuredText(s)}</td>
              <td data-label="Laagst – hoogst" className={s.measured ? 'cijfers' : 'hist-geen'}>{rangeText(s)}</td>
              <td data-label="Doel" className={s.target ? 'cijfers' : 'hist-geen'}>{targetText(s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
