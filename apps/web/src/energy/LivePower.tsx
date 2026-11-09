import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { LivePower } from '../api/types';
import { ErrorNote, QualityBadge } from '../components/bits';
import { useConnection } from '../state';
import { watts } from '../format';
import { zonedParts } from './period';

function clock(at: string, tz?: string): string {
  const p = zonedParts(at, tz);
  return `${String(p.h).padStart(2, '0')}:${String(p.min).padStart(2, '0')}`;
}

/** Current power per part. Kept fresh by WS `energy.live` (patched into qk.energyLive by ws.ts, item 36);
 * polled only as a fallback while the live connection is down. */
export function LivePowerCard({ homeId, tz }: { homeId: string; tz?: string }) {
  const { wsState } = useConnection();
  const q = useQuery({
    queryKey: qk.energyLive(homeId),
    queryFn: () => api.energyLive(homeId),
    refetchInterval: wsState === 'open' ? 120_000 : 15_000,
  });
  const p: LivePower | null | undefined = q.data && typeof q.data === 'object' && !Array.isArray(q.data) ? q.data : q.data === undefined ? undefined : null;
  return (
    <section aria-labelledby="live-h" className="card live en-live" data-testid="live-power">
      <div className="section-head">
        <h2 id="live-h" className="en-h2">Nu</h2>
        {p?.at && <span className="small muted">gemeten om {clock(p.at, tz)}</span>}
      </div>
      {q.error && !p && <ErrorNote error={q.error} />}
      {p === null && <p className="small muted" style={{ margin: 0 }}>Nog geen actueel vermogen. Is de P1-meter gekoppeld (Apparaten)?</p>}
      {p && (
        <>
          <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
            <span className="big-num en-live-total" data-testid="live-total">{watts(p.total_w)}</span>
            <span className="small muted">huis totaal</span>
          </div>
          {!p.complete && <p className="note warn" style={{ margin: 0 }}>{p.note ?? 'Niet alle meters zijn actueel; de verdeling is een schatting.'}</p>}
          {p.parts.length > 0 && (
            <ul className="meter-list en-live-parts">
              {p.parts.map((part) => (
                <li key={part.role}>
                  <span>{part.label}</span>
                  <span className="row" style={{ gap: 8 }}>
                    <span>{part.watts === null ? '– W' : watts(part.watts)}</span>
                    <QualityBadge quality={part.quality} />
                    {part.stale && <span className="small muted">verouderd</span>}
                  </span>
                  {part.note && <span className="small muted" style={{ width: '100%' }}>{part.note}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
