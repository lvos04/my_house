import type { Period } from '../api/types';
import { Tabs } from '../components/Tabs';
import { canGoNext, isCurrentPeriod, isValidDay, PERIOD_LABEL, periodTitle, shiftPeriod } from './period';

const PERIODS: Period[] = ['day', 'week', 'month', 'year'];
const PREV_LABEL: Record<Period, string> = { day: 'Vorige dag', week: 'Vorige week', month: 'Vorige maand', year: 'Vorig jaar' };
const NEXT_LABEL: Record<Period, string> = { day: 'Volgende dag', week: 'Volgende week', month: 'Volgende maand', year: 'Volgend jaar' };
const NOW_LABEL: Record<Period, string> = { day: 'Vandaag', week: 'Deze week', month: 'Deze maand', year: 'Dit jaar' };

/**
 * Period switch, previous/next and a date picker. `date` is a day in the home's time zone; `onDate(null)`
 * means "follow today" (the page then rolls over at the home's midnight).
 *
 * The switch is a tab set: the page wraps the period's figures in `<TabPanel idBase={idBase} value={period}>`
 * (the selected tab points to it with aria-controls, the panel is labelled by the tab).
 */
export function PeriodNav({
  idBase,
  period,
  date,
  today,
  onPeriod,
  onDate,
}: {
  idBase: string;
  period: Period;
  date: string;
  today: string;
  onPeriod: (p: Period) => void;
  onDate: (d: string | null) => void;
}) {
  // A day in the current period (or later) means "follow today".
  const go = (d: string) => onDate(d >= today || isCurrentPeriod(d, period, today) ? null : d);
  const current = isCurrentPeriod(date, period, today);
  const next = canGoNext(date, period, today);
  return (
    <div className="en-nav" role="group" aria-label="Periode kiezen">
      <Tabs label="Periode" items={PERIODS.map((p) => ({ key: p, label: PERIOD_LABEL[p] }))} value={period} onChange={onPeriod} className="tabs en-tabs" idBase={idBase} hasPanel />
      <div className="en-nav-row">
        <button type="button" className="icon-btn" aria-label={`${PREV_LABEL[period]}: ${periodTitle(shiftPeriod(date, period, -1), period, today)}`} onClick={() => go(shiftPeriod(date, period, -1))}>
          <span aria-hidden="true">‹</span>
        </button>
        <label className="en-date">
          <span className="sr-only">Datum</span>
          <input
            type="date"
            value={date}
            max={today}
            onChange={(e) => {
              const v = e.target.value;
              if (isValidDay(v)) go(v > today ? today : v);
            }}
          />
        </label>
        <button
          type="button"
          className="icon-btn"
          aria-label={next ? `${NEXT_LABEL[period]}: ${periodTitle(shiftPeriod(date, period, 1), period, today)}` : `${NEXT_LABEL[period]} (nog niet begonnen)`}
          disabled={!next}
          onClick={() => go(shiftPeriod(date, period, 1))}
        >
          <span aria-hidden="true">›</span>
        </button>
        {!current && (
          <button type="button" className="btn small" onClick={() => onDate(null)}>{NOW_LABEL[period]}</button>
        )}
      </div>
    </div>
  );
}
