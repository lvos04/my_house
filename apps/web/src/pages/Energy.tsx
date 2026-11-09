import '../energy/energy.css';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Period } from '../api/types';
import { useHome } from '../state';
import { useCan } from '../user';
import { ErrorNote, Loading, QualityBadge } from '../components/bits';
import { kwh } from '../format';
import { useHomeTimezone, useToday } from '../energy/hooks';
import { periodLastDay, periodStart, periodTitle, relativeLabel } from '../energy/period';
import { PeriodNav } from '../energy/PeriodNav';
import { ChartLegend, EnergyChart, EnergyTable } from '../energy/EnergyChart';
import { CostCard, MeterCards } from '../energy/Summary';
import { LivePowerCard } from '../energy/LivePower';
import { ExportCard } from '../energy/Export';
import { TariffSection } from '../energy/Tariffs';
import { LimitsCard } from '../energy/Limits';

const PER: Record<Period, string> = { day: 'uur', week: 'dag', month: 'dag', year: 'maand' };

export function EnergyPage() {
  const { homeId } = useHome();
  const can = useCan();
  const tz = useHomeTimezone(homeId);
  const today = useToday(tz);
  const [period, setPeriod] = useState<Period>('month');
  /** null = follow today (rolls over at the home's midnight). */
  const [anchor, setAnchor] = useState<string | null>(null);
  const date = anchor ?? today;
  const start = date ? periodStart(date, period) : null;
  const s = useQuery({
    queryKey: qk.energy(homeId ?? '', period, start ?? ''),
    queryFn: () => api.energySummary(homeId!, period, start!),
    enabled: !!homeId && !!start,
    refetchInterval: anchor === null ? 300_000 : false,
  });
  if (!homeId || !tz || !today || !date || !start) return <Loading />;
  const data = s.data && typeof s.data === 'object' && !Array.isArray(s.data) && 'buckets' in s.data ? s.data : null;
  const last = periodLastDay(date, period);
  const rel = relativeLabel(date, period, today);

  return (
    <>
      <header className="page-head">
        <div>
          <span className="eyebrow">Energie{rel ? ` · ${rel}` : ''}</span>
          <h1 data-testid="energy-title">{periodTitle(date, period, today)}</h1>
        </div>
      </header>
      <PeriodNav period={period} date={date} today={today} onPeriod={setPeriod} onDate={setAnchor} />
      {anchor === null && <LivePowerCard homeId={homeId} tz={tz} />}
      {s.isLoading && <Loading />}
      {s.error && !data && <ErrorNote error={s.error} />}
      {data && (
        <>
          <MeterCards s={data} />
          <section aria-labelledby="trend-h" className="card live">
            <div className="section-head">
              <h2 id="trend-h" className="en-h2">Verbruik per {PER[period]}</h2>
              <span className="small muted">Totaal {kwh(data.total_kwh)}</span>
            </div>
            <ChartLegend summary={data} />
            <EnergyChart summary={data} />
            <details>
              <summary>Toon als tabel, met kwaliteit per {PER[period]}</summary>
              <EnergyTable summary={data} />
            </details>
          </section>
          <CostCard s={data} isAdmin={can.admin} />
        </>
      )}
      <div className="grid-auto">
        <section aria-labelledby="meet-h" className="card">
          <h2 id="meet-h" className="en-h2">Wat wordt gemeten</h2>
          <ul className="meter-list" style={{ lineHeight: 1.45 }}>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><QualityBadge quality="measured" /><span>Huis totaal (P1), airco totaal (kWh-meter), boiler (Energy Socket) en gas als de P1-meter dat doorgeeft.</span></li>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><QualityBadge quality="derived" /><span>Overig = P1 − airco − boiler: apparaten zonder eigen meter.</span></li>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><QualityBadge quality="estimated" /><span>Geschat: er ontbreken metingen, of een verdachte meterstand is overgeslagen. Zie de opmerking bij de meter of de periode.</span></li>
            <li style={{ justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start' }}><QualityBadge quality="missing" /><span>Verbruik per binnenunit: beide units delen één buitenunit en worden samen gemeten.</span></li>
          </ul>
          <p className="small muted" style={{ margin: 0 }}>De boilerstekker wordt alleen uitgelezen; deze app schakelt hem nooit.</p>
        </section>
        <ExportCard homeId={homeId} from={start} to={last > today ? today : last} today={today} />
      </div>
      <TariffSection homeId={homeId} currency={data?.currency ?? 'EUR'} today={today} isAdmin={can.admin} />
      {can.admin && <LimitsCard />}
    </>
  );
}
