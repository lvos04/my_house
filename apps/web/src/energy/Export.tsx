import { useEffect, useState } from 'react';
import { exportHref } from '../api/client';
import type { ExportFormat, ExportResolution } from '../api/types';
import { useCanAct } from '../state';
import { daysInclusive, isValidDay } from './period';

export const RAW_MAX_DAYS = 31;

const RESOLUTIONS: { key: ExportResolution; label: string; help: string }[] = [
  { key: 'hour', label: 'Per uur', help: 'Uurtotalen per meter (aanbevolen).' },
  { key: 'day', label: 'Per dag', help: 'Dagtotalen per meter; klein bestand, ook voor een heel jaar.' },
  { key: 'raw', label: 'Ruwe metingen', help: `Alle opgeslagen metingen; maximaal ${RAW_MAX_DAYS} dagen per keer.` },
];

/** Why the chosen export cannot be downloaded, or null. */
export function exportProblem(from: string, to: string, resolution: ExportResolution): string | null {
  if (!isValidDay(from) || !isValidDay(to)) return 'Kies een begin- en einddatum.';
  if (from > to) return 'De begindatum ligt na de einddatum.';
  if (resolution === 'raw' && daysInclusive(from, to) > RAW_MAX_DAYS)
    return `Ruwe metingen kun je per maximaal ${RAW_MAX_DAYS} dagen exporteren (nu ${daysInclusive(from, to)}). Kies een kortere periode of uur- of dagtotalen.`;
  return null;
}

/** Streamed CSV/JSON export with aggregated (hour/day) or raw data (verbeterlijst 19/39). Disabled offline,
 * so you never download an error page. */
export function ExportCard({ homeId, from: initialFrom, to: initialTo, today }: { homeId: string; from: string; to: string; today: string }) {
  const canAct = useCanAct();
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [resolution, setResolution] = useState<ExportResolution>('hour');
  const [from, setFrom] = useState(initialFrom);
  const [to, setTo] = useState(initialTo);
  useEffect(() => {
    setFrom(initialFrom);
    setTo(initialTo);
  }, [initialFrom, initialTo]);
  const problem = exportProblem(from, to, resolution);
  const href = exportHref(homeId, { format, from, to, resolution });
  return (
    <section aria-labelledby="export-h" className="card en-export">
      <h2 id="export-h" className="en-h2">Exporteren</h2>
      <div className="form-grid">
        <label className="field">Van<input type="date" value={from} max={today} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="field">Tot en met<input type="date" value={to} max={today} onChange={(e) => setTo(e.target.value)} /></label>
        <label className="field">
          Detail
          <select value={resolution} onChange={(e) => setResolution(e.target.value as ExportResolution)} aria-describedby="export-res-help">
            {RESOLUTIONS.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </label>
        <label className="field">
          Bestand
          <select value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)}>
            <option value="csv">CSV (Excel)</option>
            <option value="json">JSON</option>
          </select>
        </label>
      </div>
      <p id="export-res-help" className="small muted" style={{ margin: 0 }}>
        {RESOLUTIONS.find((r) => r.key === resolution)?.help}
        {format === 'csv' && ' CSV gebruikt puntkomma’s en een decimale komma; een ontbrekende waarde is een lege cel.'}
      </p>
      {problem && <p className="note warn" role="status" style={{ margin: 0 }}>{problem}</p>}
      <div className="row">
        {canAct && !problem ? (
          <a className="btn primary" href={href} download data-testid="export-link">Downloaden</a>
        ) : (
          <button type="button" className="btn primary" disabled data-testid="export-link">Downloaden</button>
        )}
        {!canAct && <span className="small muted">Exporteren kan alleen met verbinding met de server.</span>}
      </div>
    </section>
  );
}
