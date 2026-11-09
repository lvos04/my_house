import { useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { CalibrationIn, Device } from '../api/types';
import { ActionButton, ErrorNote } from '../components/bits';

export const CALIBRATABLE = new Set(['zigbee_sensor', 'ac_unit']);

function parse(s: string): number | null {
  const v = s.trim().replace(/\s/g, '').replace('−', '-').replace(',', '.');
  if (!v) return 0;
  return /^[-+]?\d+(\.\d+)?$/.test(v) ? Number(v) : null;
}
const show = (v: number) => (v ? String(v).replace('.', ',') : '');

/** Body for PATCH /devices/{id}/calibration with only the changed offsets, or the Dutch problems. */
export function calibrationBody(
  d: Pick<Device, 'calibration'>,
  form: { temperature: string; humidity: string },
  withHumidity: boolean,
): { body: CalibrationIn; errors: string[] } {
  const errors: string[] = [];
  const body: CalibrationIn = {};
  const t = parse(form.temperature);
  if (t === null || t < -10 || t > 10) errors.push('Temperatuurcorrectie: vul een getal tussen −10 en 10 in.');
  else if (t !== (d.calibration?.temperature_offset ?? 0)) body.temperature_offset = t;
  if (withHumidity) {
    const h = parse(form.humidity);
    if (h === null || h < -30 || h > 30) errors.push('Vochtigheidscorrectie: vul een getal tussen −30 en 30 in.');
    else if (h !== (d.calibration?.humidity_offset ?? 0)) body.humidity_offset = h;
  }
  return { body, errors };
}

/** Offsets for a sensor or airco unit (verbeterlijst 8). They apply to new readings; the raw value is kept. */
export function CalibrationForm({ device, homeId }: { device: Device; homeId: string }) {
  const qc = useQueryClient();
  const withHumidity = device.kind === 'zigbee_sensor';
  const [form, setForm] = useState({ temperature: show(device.calibration?.temperature_offset ?? 0), humidity: show(device.calibration?.humidity_offset ?? 0) });
  const [errors, setErrors] = useState<string[]>([]);
  const [nothing, setNothing] = useState(false);
  const save = useMutation({
    mutationFn: (body: CalibrationIn) => api.calibrateDevice(device.id, body),
    onSuccess: (d) => {
      qc.setQueryData<Device[]>(qk.devices(homeId), (l) => l?.map((x) => (x.id === d.id ? d : x)));
      qc.invalidateQueries({ queryKey: qk.devices(homeId) });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const { body, errors: errs } = calibrationBody(device, form, withHumidity);
    setErrors(errs);
    setNothing(!errs.length && !Object.keys(body).length);
    if (errs.length || !Object.keys(body).length) return;
    save.mutate(body);
  };
  return (
    <form className="stack tight dv-calibration" onSubmit={submit} aria-label={`Kalibratie ${device.name}`} noValidate>
      <h4>Kalibratie</h4>
      <p className="small muted" style={{ margin: 0 }}>
        Meet de sensor te warm of te koud? Vul de correctie in, bijvoorbeeld −0,5 als hij een halve graad te hoog meet. Geldt voor nieuwe metingen; de ruwe waarde wordt bewaard.
      </p>
      <div className="form-grid">
        <label className="field">
          Temperatuurcorrectie (°C)
          <input inputMode="decimal" placeholder="0" value={form.temperature} onChange={(e) => setForm({ ...form, temperature: e.target.value })} />
        </label>
        {withHumidity && (
          <label className="field">
            Vochtigheidscorrectie (%)
            <input inputMode="decimal" placeholder="0" value={form.humidity} onChange={(e) => setForm({ ...form, humidity: e.target.value })} />
          </label>
        )}
        <ActionButton type="submit" className="btn" disabled={save.isPending}>Correctie opslaan</ActionButton>
      </div>
      {errors.length > 0 && (
        <div className="note error" role="alert"><ul className="plain-list">{errors.map((e) => <li key={e}>{e}</li>)}</ul></div>
      )}
      {nothing && <span className="small muted" role="status">Niets gewijzigd.</span>}
      {save.isSuccess && !nothing && <span className="small muted" role="status">Opgeslagen. Geldt voor nieuwe metingen.</span>}
      {save.error && <ErrorNote error={save.error} />}
    </form>
  );
}
