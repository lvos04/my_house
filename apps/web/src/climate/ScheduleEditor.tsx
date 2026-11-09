// Week schedule editor (verbeterlijst 12): blocks per day with start, target, mode (incl. "houd huidige
// modus") and name; copy a day to other days; templates; validation with Dutch messages (also the
// server's 422); a preview of the next change. Used for zone schedules and for comfort profiles.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { HvacMode, ScheduleBlock } from '../api/types';
import { MODE_LABEL } from '../format';
import { DAY_LABEL, DAY_SHORT } from '../i18n';
import { Tabs } from '../components/Tabs';
import { ActionButton, Empty } from '../components/bits';
import { FormError, num } from './common';
import {
  activeBlock,
  blocksToWeek,
  blockSummary,
  copyDay,
  daySegments,
  fromMin,
  isTime,
  localDayMinute,
  newBlockId,
  nextChange,
  parseTarget,
  scheduleKey,
  sortDay,
  suggestStart,
  TEMPLATE_HELP,
  TEMPLATE_LABEL,
  templateWeek,
  validateWeek,
  weekToBlocks,
  whenText,
  type EditBlock,
  type Issue,
  type Range,
  type TemplateKind,
  type Week,
} from './schedule';

const ALL_MODES: HvacMode[] = ['heat', 'cool', 'auto', 'dry', 'fan'];
const WORKDAYS = [0, 1, 2, 3, 4];
const WEEKEND = [5, 6];
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function useMinuteClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tm = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(tm);
  }, []);
  return now;
}

/** Seven bars, one per day: warm (orange) for the higher targets, cool (blue) for the lower ones. */
export function WeekView({ week, today }: { week: Week; today: number | null }) {
  const targets = week.flat().map((b) => parseTarget(b.target)).filter(Number.isFinite);
  const lo = targets.length ? Math.min(...targets) : 0;
  const hi = targets.length ? Math.max(...targets) : 0;
  const mid = (lo + hi) / 2;
  return (
    <div className="week-view">
      {week.map((_, d) => {
        const segs = daySegments(week, d);
        const text = segs.length ? segs.map((s) => `${fromMin(s.from)} ${blockSummary(s.block)}`).join(', ') : 'geen blokken';
        return (
          <div key={d} className={`week-row${d === today ? ' current' : ''}`}>
            <span className="week-day" aria-hidden="true">{DAY_SHORT[d]}</span>
            <div className="week-bar" role="img" aria-label={`${cap(DAY_LABEL[d])}${d === today ? ' (vandaag)' : ''}: ${text}`}>
              {segs.map((s, i) => {
                const tv = parseTarget(s.block.target);
                const cls = s.block.mode === 'off' ? 'off' : hi === lo || tv > mid ? 'warm' : 'cold';
                return <div key={i} className={`${cls}${s.carried ? ' carried' : ''}`} style={{ flex: s.to - s.from }} title={`${fromMin(s.from)} ${blockSummary(s.block)}`} />;
              })}
            </div>
          </div>
        );
      })}
      <div className="week-axis" aria-hidden="true">
        <span />
        <div><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
      </div>
    </div>
  );
}

function Preview({ week, tz, dirty }: { week: Week; tz?: string; dirty: boolean }) {
  const now = useMinuteClock();
  const active = activeBlock(week, now, tz);
  const next = nextChange(week, now, tz);
  return (
    <div className="sched-preview" aria-live="polite">
      {dirty && <span className="small muted">Voorbeeld volgens het schema hieronder (nog niet opgeslagen):</span>}
      {next ? (
        <>
          <span>Nu: {active ? blockSummary(active) : '–'}</span>
          <span><strong>Volgende wijziging:</strong> {whenText(next, now, tz)} · {blockSummary(next.block)}</span>
        </>
      ) : (
        <span>Geen blokken: de regeling doet niets en de airco houdt zijn eigen instelling.</span>
      )}
    </div>
  );
}

interface DayEditorProps {
  day: number;
  week: Week;
  issues: Issue[];
  modes: HvacMode[];
  onChange: (day: number, blocks: EditBlock[]) => void;
}

function DayEditor({ day, week, issues, modes, onChange }: DayEditorProps) {
  const blocks = week[day];
  const set = (id: number, patch: Partial<EditBlock>) => onChange(day, blocks.map((b) => (b.id === id ? { ...b, ...patch } : b)));
  const carried = daySegments(week, day).find((s) => s.carried);
  const add = () => {
    const last = sortDay(blocks).at(-1);
    onChange(day, [...blocks, { id: newBlockId(), start: suggestStart(blocks), target: last?.target ?? '20', mode: last?.mode ?? null, label: '' }]);
  };
  return (
    <div className="stack">
      {blocks.length === 0 && (
        <Empty>
          Geen blokken op {DAY_LABEL[day]}.{' '}
          {carried ? `Het blok van eerder (${blockSummary(carried.block)}) loopt de hele dag door.` : ''}
        </Empty>
      )}
      {blocks.length > 0 && (
        <ul className="sched-blocks" aria-label={`Blokken op ${DAY_LABEL[day]}`}>
          {blocks.map((b, i) => {
            const mine = issues.filter((x) => x.blockId === b.id);
            const errId = `blk-err-${b.id}`;
            const bad = (f: Issue['field']) => mine.some((x) => x.field === f);
            const desc = mine.length ? errId : undefined;
            const opts = b.mode && b.mode !== 'off' && !modes.includes(b.mode) ? [...modes, b.mode] : modes;
            return (
              <li key={b.id} className={`sched-block${mine.length ? ' invalid' : ''}`}>
                <label className="field">
                  <span>Begintijd<span className="sr-only"> blok {i + 1}</span></span>
                  <input
                    type="time"
                    step={300}
                    required
                    value={b.start}
                    aria-invalid={bad('start') || undefined}
                    aria-describedby={desc}
                    onChange={(e) => set(b.id, { start: e.target.value })}
                    onBlur={() => onChange(day, sortDay(blocks))}
                  />
                </label>
                <label className="field">
                  <span>Doel °C<span className="sr-only"> blok {i + 1}</span></span>
                  <input
                    type="text"
                    inputMode="decimal"
                    autoComplete="off"
                    required
                    value={b.target}
                    aria-invalid={bad('target') || undefined}
                    aria-describedby={desc}
                    onChange={(e) => set(b.id, { target: e.target.value })}
                  />
                </label>
                <label className="field f-mode">
                  <span>Modus<span className="sr-only"> blok {i + 1}</span></span>
                  <select value={b.mode ?? ''} aria-invalid={bad('mode') || undefined} aria-describedby={desc} onChange={(e) => set(b.id, { mode: (e.target.value || null) as HvacMode | null })}>
                    <option value="">Houd huidige modus</option>
                    {opts.map((m) => <option key={m} value={m}>{MODE_LABEL[m] ?? m}</option>)}
                    <option value="off">Uit (zet de airco uit)</option>
                  </select>
                </label>
                <label className="field">
                  <span>Naam<span className="sr-only"> blok {i + 1}</span></span>
                  <input maxLength={50} placeholder="bijv. Nacht" value={b.label} aria-invalid={bad('label') || undefined} aria-describedby={desc} onChange={(e) => set(b.id, { label: e.target.value })} />
                </label>
                <button type="button" className="btn small danger remove" aria-label={`Blok ${i + 1} (${b.start || 'zonder tijd'}) op ${DAY_LABEL[day]} verwijderen`} onClick={() => onChange(day, blocks.filter((x) => x.id !== b.id))}>
                  Verwijderen
                </button>
                {mine.length > 0 && (
                  <div id={errId} className="error-text" style={{ gridColumn: '1 / -1' }}>
                    {mine.map((x) => <div key={x.field + x.message}>{x.message}</div>)}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <div className="c-actions">
        <button type="button" className="btn" onClick={add}>Blok toevoegen</button>
        {blocks.length > 0 && <button type="button" className="btn ghost" onClick={() => onChange(day, [])}>Dag leegmaken</button>}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Zonder modus houdt een blok de modus waarin de airco nu draait en past alleen de doeltemperatuur aan; een uitgeschakelde airco blijft dan uit. Met een modus zet het blok de airco in die modus (en dus ook aan).
      </p>
    </div>
  );
}

function CopyTool({ day, onCopy }: { day: number; onCopy: (to: number[]) => void }) {
  const [to, setTo] = useState<number[]>([]);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    setTo([]);
    setDone(null);
  }, [day]);
  const others = [0, 1, 2, 3, 4, 5, 6].filter((d) => d !== day);
  const pick = (days: number[]) => setTo(days.filter((d) => d !== day));
  const toggle = (d: number) => setTo((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d].sort()));
  const copy = () => {
    onCopy(to);
    setDone(`${cap(DAY_LABEL[day])} gekopieerd naar ${to.map((d) => DAY_LABEL[d]).join(', ')}.`);
    setTo([]);
  };
  return (
    <fieldset className="sched-tool">
      <legend className="sr-only">Dag kopiëren</legend>
      <h4 aria-hidden="true">Dag kopiëren</h4>
      <span className="small muted">Kopieer de blokken van {DAY_LABEL[day]} naar (vervangt die dagen):</span>
      <div className="day-checks">
        {others.map((d) => (
          <label key={d} className="check-row">
            <input type="checkbox" checked={to.includes(d)} onChange={() => toggle(d)} />
            {cap(DAY_LABEL[d])}
          </label>
        ))}
      </div>
      <div className="c-actions">
        <button type="button" className="btn small" onClick={() => pick(WORKDAYS)}>Werkdagen</button>
        <button type="button" className="btn small" onClick={() => pick(WEEKEND)}>Weekend</button>
        <button type="button" className="btn small" onClick={() => pick(others)}>Alle dagen</button>
      </div>
      <button type="button" className="btn" disabled={!to.length} onClick={copy}>
        {to.length ? `Kopiëren naar ${to.length} ${to.length === 1 ? 'dag' : 'dagen'}` : 'Kies eerst dagen'}
      </button>
      <span className="small muted" aria-live="polite">{done}</span>
    </fieldset>
  );
}

function TemplateTool({ range, modes, onApply }: { range?: Range | null; modes: HvacMode[]; onApply: (w: Week) => void }) {
  const [kind, setKind] = useState<TemplateKind>('nachtverlaging');
  const [comfort, setComfort] = useState('21');
  const [low, setLow] = useState('18');
  const [mode, setMode] = useState<HvacMode | ''>('');
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const lo = Math.max(10, range?.min_c ?? 10);
  const hi = Math.min(30, range?.max_c ?? 30);
  const apply = () => {
    const c = num(comfort);
    const l = kind === 'constant' ? c : num(low);
    if (![c, l].every((v) => Number.isFinite(v) && v >= lo && v <= hi)) {
      setErr(`Kies temperaturen tussen ${lo} en ${hi} °C.`);
      setDone(null);
      return;
    }
    setErr(null);
    onApply(templateWeek(kind, { comfort: c, low: l, mode: mode || null }));
    setDone(`Sjabloon ${TEMPLATE_LABEL[kind]} ingevuld. Controleer het schema en druk op Opslaan.`);
  };
  return (
    <div className="sched-tool" role="group" aria-labelledby="tpl-h">
      <h4 id="tpl-h">Sjabloon</h4>
      <label className="field">Sjabloon kiezen
        <select value={kind} onChange={(e) => setKind(e.target.value as TemplateKind)}>
          {(Object.keys(TEMPLATE_LABEL) as TemplateKind[]).map((k) => <option key={k} value={k}>{TEMPLATE_LABEL[k]}</option>)}
        </select>
      </label>
      <span className="small muted">{TEMPLATE_HELP[kind]}</span>
      <div className="form-grid">
        <label className="field">Comfort °C<input inputMode="decimal" value={comfort} onChange={(e) => setComfort(e.target.value)} /></label>
        {kind !== 'constant' && <label className="field">Lager °C<input inputMode="decimal" value={low} onChange={(e) => setLow(e.target.value)} /></label>}
      </div>
      <label className="field">Modus in sjabloon
        <select value={mode} onChange={(e) => setMode(e.target.value as HvacMode | '')}>
          <option value="">Houd huidige modus</option>
          {modes.map((m) => <option key={m} value={m}>{MODE_LABEL[m] ?? m}</option>)}
        </select>
      </label>
      <span className="small muted">Vervangt het hele weekschema hieronder. Er wordt pas iets opgeslagen als je op Opslaan drukt.</span>
      <button type="button" className="btn" onClick={apply}>Sjabloon toepassen</button>
      {err && <span className="error-text" role="alert">{err}</span>}
      <span className="small muted" aria-live="polite">{done}</span>
    </div>
  );
}

export interface ScheduleEditorProps {
  idBase: string;
  /** What the server has now. */
  blocks: ScheduleBlock[];
  /** Unit range and modes; null for a profile or a zone without airco. */
  range?: Range | null;
  tz?: string;
  editable: boolean;
  onSave?: (blocks: ScheduleBlock[]) => Promise<unknown>;
  intro?: ReactNode;
  saveLabel?: string;
}

export function ScheduleEditor({ idBase, blocks, range, tz, editable, onSave, intro, saveLabel = 'Weekschema opslaan' }: ScheduleEditorProps) {
  const serverKey = scheduleKey(blocks);
  const [week, setWeek] = useState<Week>(() => blocksToWeek(blocks));
  const [base, setBase] = useState(serverKey);
  const today = useMemo(() => localDayMinute(new Date(), tz).day, [tz]);
  const [day, setDay] = useState(today >= 0 ? today : 0);
  const [tried, setTried] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [changedElsewhere, setChangedElsewhere] = useState(false);

  const editedKey = scheduleKey(weekToBlocks(week));
  const dirty = editedKey !== base;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // Follow the server while nothing is edited; warn when it changes under an edit.
  useEffect(() => {
    if (serverKey === base) return;
    if (!dirtyRef.current || editedKey === serverKey) {
      setWeek(blocksToWeek(blocks));
      setBase(serverKey);
      setChangedElsewhere(false);
    } else {
      setChangedElsewhere(true);
    }
  }, [serverKey]);

  const issues = useMemo(() => validateWeek(week, range), [week, range]);
  const modes = range?.modes.length ? range.modes.filter((m) => m !== 'off') : ALL_MODES;

  const edit = (w: Week) => {
    setWeek(w);
    setSaved(false);
    setSaveError(null);
  };
  const changeDay = (d: number, list: EditBlock[]) => edit(week.map((x, i) => (i === d ? list : x)));

  const save = async () => {
    setTried(true);
    setSaved(false);
    setSaveError(null);
    if (issues.length || !onSave) return;
    setSaving(true);
    try {
      const out = weekToBlocks(week);
      await onSave(out);
      setBase(scheduleKey(out));
      setChangedElsewhere(false);
      setSaved(true);
      setTried(false);
    } catch (e) {
      setSaveError(e);
    } finally {
      setSaving(false);
    }
  };
  const undo = () => {
    setWeek(blocksToWeek(blocks));
    setBase(serverKey);
    setTried(false);
    setSaveError(null);
    setChangedElsewhere(false);
  };

  const dayItems = DAY_SHORT.map((s, d) => ({
    key: `d${d}`,
    label: (
      <>
        <span aria-hidden="true">{s}</span>
        <span className="sr-only">{DAY_LABEL[d]}{week[d].length ? `, ${week[d].length} ${week[d].length === 1 ? 'blok' : 'blokken'}` : ', geen blokken'}</span>
      </>
    ),
  }));

  return (
    <div className="stack">
      {intro}
      <WeekView week={week} today={today >= 0 ? today : null} />
      <Preview week={week} tz={tz} dirty={dirty} />
      <Tabs label="Dag van de week" items={dayItems} value={`d${day}`} onChange={(k) => setDay(Number(k.slice(1)))} className="tabs day-tabs" idBase={`${idBase}-day`}>
        {() =>
          editable ? (
            <DayEditor day={day} week={week} issues={issues} modes={modes} onChange={changeDay} />
          ) : week[day].length ? (
            <ul className="c-list" aria-label={`Blokken op ${DAY_LABEL[day]}`}>
              {sortDay(week[day]).map((b) => (
                <li key={b.id}><strong>{isTime(b.start) ? b.start : '–'}</strong> · {blockSummary(b)}</li>
              ))}
            </ul>
          ) : (
            <Empty>Geen blokken op {DAY_LABEL[day]}.</Empty>
          )
        }
      </Tabs>
      {editable && (
        <>
          <div className="sched-tools">
            <CopyTool day={day} onCopy={(to) => edit(copyDay(week, day, to))} />
            <TemplateTool range={range} modes={modes} onApply={edit} />
          </div>
          {changedElsewhere && (
            <div className="note warn">Het schema is intussen ergens anders gewijzigd. Opslaan overschrijft die wijziging; kies Ongedaan maken om de nieuwe versie te laden.</div>
          )}
          {tried && issues.length > 0 && (
            <div className="note error" role="alert">
              Het schema is nog niet opgeslagen. Controleer het volgende:
              <ul className="plain-list">
                {issues.map((x, i) => (
                  <li key={i}>
                    {x.message}{' '}
                    {x.day !== day && x.index >= 0 && (
                      <button type="button" className="btn ghost small" onClick={() => setDay(x.day)}>Naar {DAY_LABEL[x.day]}</button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <FormError error={saveError} />
          <div className="sched-save" data-actiebalk="">
            <ActionButton className="btn primary" disabled={saving || !dirty} onClick={save}>{saving ? 'Opslaan…' : saveLabel}</ActionButton>
            <button type="button" className="btn" disabled={!dirty || saving} onClick={undo}>Ongedaan maken</button>
            <span className="small muted" aria-live="polite">
              {saved && !dirty ? 'Opgeslagen.' : dirty ? 'Niet opgeslagen wijzigingen.' : ''}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
