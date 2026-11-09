import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { CompatibilityMatrix } from '../api/types';
import { ErrorNote, Loading } from '../components/bits';
import { COMPATIBILITY_LABEL, PROVIDER_LABEL, t } from '../i18n';
import { CompatBadge } from './DeviceCard';

/** Which models work locally, what needs the manufacturer's app, and how far each is tested in this
 * home (verbeterlijst 29). Loaded when opened. */
export function CompatibilitySection() {
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: qk.compatibility, queryFn: () => api.compatibility(), enabled: open, staleTime: 5 * 60_000 });
  const m: CompatibilityMatrix | null = q.data && typeof q.data === 'object' && !Array.isArray(q.data) && Array.isArray(q.data.rows) ? q.data : null;
  const providerLabel = (p: string) => m?.providers.find((x) => x.provider === p)?.label ?? t(PROVIDER_LABEL, p);
  return (
    <details className="card dv-compat-card" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary><h2 style={{ display: 'inline', fontSize: 18 }}>Welke apparaten werken?</h2></summary>
      {q.isLoading && <Loading />}
      {q.error && <ErrorNote error={q.error} />}
      {m && (
        <>
          <p className="small muted" style={{ margin: 0 }}>{m.note}</p>
          <div className="table-wrap">
            <table className="stack-mobile dv-compat-table">
              <thead>
                <tr>
                  <th scope="col">Merk</th>
                  <th scope="col">Model</th>
                  <th scope="col">Werkt lokaal</th>
                  <th scope="col">Eerst in de app van de fabrikant</th>
                  <th scope="col">Status</th>
                  <th scope="col">In dit huis</th>
                </tr>
              </thead>
              <tbody>
                {m.rows.map((r) => (
                  <tr key={`${r.provider}-${r.model}`}>
                    <td data-label="Merk">{providerLabel(r.provider)}</td>
                    <th scope="row" data-label="Model">{r.model}<div className="small muted">{r.protocol}</div></th>
                    <td data-label="Werkt lokaal">{r.local}</td>
                    <td data-label="Eerst in de app van de fabrikant">{r.vendor_app ?? 'Niets'}</td>
                    <td data-label="Status"><CompatBadge c={r.status} /></td>
                    <td data-label="In dit huis">
                      {r.devices.length === 0 ? (
                        <span className="small muted">geen</span>
                      ) : (
                        <ul className="plain-list small">
                          {r.devices.map((d) => <li key={d.id}>{d.name} · {t(COMPATIBILITY_LABEL, d.compatibility)}</li>)}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  );
}
