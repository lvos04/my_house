import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { EnergyLimits, EnergyLimitsIn } from '../api/types';
import { ActionButton, ErrorNote } from '../components/bits';
import { parseDecimal } from './Tariffs';

const FIELDS: { key: keyof EnergyLimits; label: string; unit: string }[] = [
  { key: 'grid', label: 'Huis totaal (P1)', unit: 'kW' },
  { key: 'airco', label: 'Airco (kWh-meter)', unit: 'kW' },
  { key: 'boiler', label: 'Boiler (stekker)', unit: 'kW' },
  { key: 'gas', label: 'Gas', unit: 'm³ per uur' },
];

/** Maximum plausible power per meter: a step above it counts as a suspect reading and is skipped (item 20). Admin only. */
export function LimitsCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.energyLimits, queryFn: () => api.energyLimits() });
  const [form, setForm] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const data = q.data && typeof q.data === 'object' && !Array.isArray(q.data) ? q.data : null;
  useEffect(() => {
    if (data) setForm(Object.fromEntries(FIELDS.map((f) => [f.key, String(data[f.key]).replace('.', ',')])));
  }, [data]);
  const save = useMutation({
    mutationFn: (body: EnergyLimitsIn) => api.putEnergyLimits(body),
    onSuccess: (v) => qc.setQueryData(qk.energyLimits, v),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!data) return;
    const body: EnergyLimitsIn = {};
    for (const f of FIELDS) {
      const v = parseDecimal(form[f.key] ?? '');
      if (v === null) body[f.key] = null;
      else if (Number.isNaN(v) || v < 0.1 || v > 100) {
        setProblem(`${f.label}: vul een getal tussen 0,1 en 100 in (leeg = standaard).`);
        return;
      } else if (v !== data[f.key]) body[f.key] = v;
    }
    setProblem(null);
    save.mutate(body);
  };
  return (
    <details className="card en-limits">
      <summary><h2 className="en-h2" style={{ display: 'inline' }}>Meetgrenzen</h2></summary>
      <p className="small muted" style={{ margin: 0 }}>
        Een meterstand die meer verbruik betekent dan dit maximale vermogen, telt als verdacht en wordt overgeslagen (zichtbaar als opmerking bij de periode). Leeg = standaardwaarde.
      </p>
      {q.error && <ErrorNote error={q.error} />}
      {data && (
        <form className="stack" onSubmit={submit} aria-label="Meetgrenzen">
          <div className="form-grid">
            {FIELDS.map((f) => (
              <label key={f.key} className="field">
                {f.label} ({f.unit})
                <input inputMode="decimal" value={form[f.key] ?? ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
              </label>
            ))}
          </div>
          {problem && <p className="small error-text" role="alert" style={{ margin: 0 }}>{problem}</p>}
          <div className="row">
            <ActionButton type="submit" className="btn" disabled={save.isPending}>Opslaan</ActionButton>
            {save.isSuccess && <span className="small muted" role="status">Opgeslagen.</span>}
          </div>
          {save.error && <ErrorNote error={save.error} />}
        </form>
      )}
    </details>
  );
}
