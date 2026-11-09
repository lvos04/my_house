import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Tariff, TariffIn } from '../api/types';
import { ActionButton, Empty, ErrorNote, Loading } from '../components/bits';

/* ---------- form model (pure, tested) ---------- */

export interface TariffForm {
  valid_from: string;
  import_per_kwh: string;
  export_per_kwh: string;
  fixed_per_day: string;
  /** Separate prices for normal (T1) and off-peak (T2). */
  dual: boolean;
  import_t1_per_kwh: string;
  import_t2_per_kwh: string;
  gas_per_m3: string;
}
type PriceField = 'import_per_kwh' | 'export_per_kwh' | 'fixed_per_day' | 'import_t1_per_kwh' | 'import_t2_per_kwh' | 'gas_per_m3';

export const EMPTY_FORM: TariffForm = {
  valid_from: '',
  import_per_kwh: '',
  export_per_kwh: '',
  fixed_per_day: '',
  dual: false,
  import_t1_per_kwh: '',
  import_t2_per_kwh: '',
  gas_per_m3: '',
};

const RANGES: Record<PriceField, [number, number, string]> = {
  import_per_kwh: [0, 5, 'Stroomprijs'],
  export_per_kwh: [-1, 5, 'Terugleververgoeding'],
  fixed_per_day: [0, 50, 'Vaste kosten per dag'],
  import_t1_per_kwh: [0, 5, 'Prijs normaal tarief (T1)'],
  import_t2_per_kwh: [0, 5, 'Prijs daltarief (T2)'],
  gas_per_m3: [0, 20, 'Gasprijs'],
};

/** "0,25" / "0.25" -> 0.25; "" -> null; anything else -> NaN. */
export function parseDecimal(s: string): number | null {
  const v = s.trim().replace(/\s/g, '');
  if (!v) return null;
  if (!/^-?\d+([.,]\d+)?$/.test(v)) return Number.NaN;
  return Number(v.replace(',', '.'));
}

/** Dutch problems per field; empty object = valid. */
export function tariffErrors(f: TariffForm): Partial<Record<keyof TariffForm, string>> {
  const e: Partial<Record<keyof TariffForm, string>> = {};
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.valid_from)) e.valid_from = 'Kies de datum waarop dit tarief ingaat.';
  const fields: PriceField[] = ['import_per_kwh', 'export_per_kwh', 'fixed_per_day', 'gas_per_m3', ...(f.dual ? (['import_t1_per_kwh', 'import_t2_per_kwh'] as PriceField[]) : [])];
  for (const k of fields) {
    const v = parseDecimal(f[k]);
    const [lo, hi, what] = RANGES[k];
    if (v === null) {
      if (k === 'import_per_kwh') e[k] = 'Vul de stroomprijs per kWh in.';
      else if (f.dual && (k === 'import_t1_per_kwh' || k === 'import_t2_per_kwh')) e[k] = 'Vul zowel T1 als T2 in, of zet dag/nacht uit.';
    } else if (Number.isNaN(v)) e[k] = `${what}: vul een getal in, bijvoorbeeld 0,25.`;
    else if (v < lo || v > hi) e[k] = `${what} moet tussen ${String(lo).replace('.', ',')} en ${String(hi).replace('.', ',')} liggen.`;
  }
  return e;
}

/** Request body: optional fields that are empty are left out (the API then uses 0 / none). Item 7. */
export function tariffPayload(f: TariffForm): TariffIn {
  const out: TariffIn = { valid_from: f.valid_from, import_per_kwh: parseDecimal(f.import_per_kwh) ?? 0 };
  const opt: PriceField[] = ['export_per_kwh', 'fixed_per_day', 'gas_per_m3', ...(f.dual ? (['import_t1_per_kwh', 'import_t2_per_kwh'] as PriceField[]) : [])];
  for (const k of opt) {
    const v = parseDecimal(f[k]);
    if (v !== null && !Number.isNaN(v)) (out as unknown as Record<string, unknown>)[k] = v;
  }
  return out;
}

const dec = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v).replace('.', ','));

export function formFromTariff(t: Tariff): TariffForm {
  const dual = t.import_t1_per_kwh !== null && t.import_t2_per_kwh !== null;
  return {
    valid_from: t.valid_from,
    import_per_kwh: dec(t.import_per_kwh),
    export_per_kwh: t.export_per_kwh ? dec(t.export_per_kwh) : '',
    fixed_per_day: t.fixed_per_day ? dec(t.fixed_per_day) : '',
    dual,
    import_t1_per_kwh: dual ? dec(t.import_t1_per_kwh) : '',
    import_t2_per_kwh: dual ? dec(t.import_t2_per_kwh) : '',
    gas_per_m3: dec(t.gas_per_m3),
  };
}

/* ---------- UI ---------- */

function price(v: number | null | undefined, currency: string): string {
  if (v === null || v === undefined) return '–';
  return v.toLocaleString('nl-NL', { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 4 });
}
function dayText(s: string): string {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
}
const SOURCE_LABEL: Record<string, string> = { handmatig: 'Handmatig', manual: 'Handmatig', import: 'Geïmporteerd' };

function PriceInput({ field, label, form, set, errors, hint }: { field: PriceField; label: string; form: TariffForm; set: (f: TariffForm) => void; errors: Partial<Record<keyof TariffForm, string>>; hint?: string }) {
  const err = errors[field];
  const id = `tariff-${field}`;
  return (
    <label className="field">
      {label}
      <input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        value={form[field]}
        aria-invalid={err ? true : undefined}
        aria-describedby={err ? `${id}-err` : hint ? `${id}-hint` : undefined}
        onChange={(e) => set({ ...form, [field]: e.target.value })}
      />
      {hint && !err && <span id={`${id}-hint`} className="small muted">{hint}</span>}
      {err && <span id={`${id}-err`} className="small error-text">{err}</span>}
    </label>
  );
}

function TariffFormView({ homeId, editing, defaultFrom, onDone, hasTariffs }: { homeId: string; editing: Tariff | null; defaultFrom: string; onDone: () => void; hasTariffs: boolean }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<TariffForm>(editing ? formFromTariff(editing) : { ...EMPTY_FORM, valid_from: defaultFrom });
  const [tried, setTried] = useState(false);
  const errors = tried ? tariffErrors(form) : {};
  const save = useMutation({
    mutationFn: (body: TariffIn) => (editing ? api.updateTariff(editing.id, body) : api.addTariff(homeId, body)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.tariffs(homeId) });
      qc.invalidateQueries({ queryKey: ['energy', homeId] });
      setForm({ ...EMPTY_FORM, valid_from: defaultFrom });
      setTried(false);
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (Object.keys(tariffErrors(form)).length) return;
    save.mutate(tariffPayload(form));
  };
  const title = editing ? `Tarief vanaf ${dayText(editing.valid_from)} wijzigen` : 'Tarief toevoegen';
  return (
    <form onSubmit={submit} className="stack en-tariff-form" aria-label={title} noValidate>
      <h3>{title}</h3>
      {!editing && !hasTariffs && (
        <p className="small muted" style={{ margin: 0 }}>Kies de begindatum van je energiecontract. Voor dagen vóór deze datum rekent de app geen kosten.</p>
      )}
      <div className="form-grid">
        <label className="field">
          Geldig vanaf
          <input type="date" required value={form.valid_from} aria-invalid={errors.valid_from ? true : undefined} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} />
          {errors.valid_from && <span className="small error-text">{errors.valid_from}</span>}
        </label>
        <PriceInput field="import_per_kwh" label="Stroomprijs € per kWh" form={form} set={setForm} errors={errors} />
        <PriceInput field="export_per_kwh" label="Teruglevering € per kWh (optioneel)" form={form} set={setForm} errors={errors} />
        <PriceInput field="fixed_per_day" label="Vaste kosten € per dag (optioneel)" form={form} set={setForm} errors={errors} />
        <PriceInput field="gas_per_m3" label="Gasprijs € per m³ (optioneel)" form={form} set={setForm} errors={errors} />
      </div>
      <label className="check-row">
        <input type="checkbox" checked={form.dual} onChange={(e) => setForm({ ...form, dual: e.target.checked })} />
        Aparte prijzen voor normaal (T1) en dal (T2)
      </label>
      {form.dual && (
        <div className="form-grid">
          <PriceInput field="import_t1_per_kwh" label="Normaal (T1) € per kWh" form={form} set={setForm} errors={errors} />
          <PriceInput field="import_t2_per_kwh" label="Dal (T2) € per kWh" form={form} set={setForm} errors={errors} />
          <p id="tariff-dual-hint" className="small muted" style={{ margin: 0, gridColumn: '1 / -1' }}>De gewone stroomprijs geldt als de P1-meter geen verdeling over T1 en T2 doorgeeft.</p>
        </div>
      )}
      <p className="small muted" style={{ margin: 0 }}>Bedragen inclusief btw; een komma of punt mag allebei. Vaste kosten per dag = vastrecht en netbeheer, minus de vermindering energiebelasting, gedeeld door 365. Laat je teruglevering of vaste kosten leeg, dan telt de app 0.</p>
      <div className="row">
        <ActionButton type="submit" className="btn primary" disabled={save.isPending}>{save.isPending ? 'Opslaan…' : editing ? 'Wijziging opslaan' : 'Tarief opslaan'}</ActionButton>
        {editing && <button type="button" className="btn" onClick={onDone}>Annuleren</button>}
      </div>
      {save.error && <ErrorNote error={save.error} />}
    </form>
  );
}

/** Tariffs: list, add, change, delete (verbeterlijst 7, 35). Viewers only see the list. */
export function TariffSection({ homeId, currency, today, isAdmin }: { homeId: string; currency: string; today: string; isAdmin: boolean }) {
  const qc = useQueryClient();
  const tariffs = useQuery({ queryKey: qk.tariffs(homeId), queryFn: () => api.tariffs(homeId) });
  const [editing, setEditing] = useState<Tariff | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteTariff(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.tariffs(homeId) });
      qc.invalidateQueries({ queryKey: ['energy', homeId] });
    },
  });
  const list = Array.isArray(tariffs.data) ? tariffs.data : [];
  const defaultFrom = list.length ? today : `${today.slice(0, 4)}-01-01`;
  return (
    <section aria-labelledby="tarief-h" className="card en-tariffs">
      <h2 id="tarief-h" className="en-h2">Tarieven</h2>
      {tariffs.isLoading && <Loading />}
      {tariffs.error && <ErrorNote error={tariffs.error} />}
      {!tariffs.isLoading && list.length === 0 && <Empty>Nog geen tarief. Zonder tarief worden geen kosten berekend.</Empty>}
      {list.length > 0 && (
        <div className="table-wrap">
          <table className="stack-mobile">
            <thead>
              <tr>
                <th scope="col">Vanaf</th>
                <th scope="col">Stroom per kWh</th>
                <th scope="col">Teruglevering per kWh</th>
                <th scope="col">Vast per dag</th>
                <th scope="col">Gas per m³</th>
                <th scope="col">Bron</th>
                {isAdmin && <th scope="col"><span className="sr-only">Acties</span></th>}
              </tr>
            </thead>
            <tbody>
              {list.map((t) => (
                <tr key={t.id}>
                  <th scope="row" data-label="Vanaf">{dayText(t.valid_from)}</th>
                  <td data-label="Stroom per kWh">
                    {price(t.import_per_kwh, currency)}
                    {t.import_t1_per_kwh !== null && t.import_t2_per_kwh !== null && (
                      <div className="small muted">T1 {price(t.import_t1_per_kwh, currency)} · T2 {price(t.import_t2_per_kwh, currency)}</div>
                    )}
                  </td>
                  <td data-label="Teruglevering per kWh">{price(t.export_per_kwh, currency)}</td>
                  <td data-label="Vast per dag">{price(t.fixed_per_day, currency)}</td>
                  <td data-label="Gas per m³">{price(t.gas_per_m3, currency)}</td>
                  <td data-label="Bron">{SOURCE_LABEL[t.source] ?? t.source}</td>
                  {isAdmin && (
                    <td data-label="Acties">
                      <div className="row" style={{ gap: 6 }}>
                        <button type="button" className="btn small" aria-label={`Tarief vanaf ${dayText(t.valid_from)} wijzigen`} onClick={() => setEditing(t)}>Wijzigen</button>
                        <ActionButton
                          className="btn small danger"
                          aria-label={`Tarief vanaf ${dayText(t.valid_from)} verwijderen`}
                          disabled={remove.isPending}
                          onClick={() => {
                            if (window.confirm(`Tarief vanaf ${dayText(t.valid_from)} verwijderen? De kosten vanaf die datum worden opnieuw berekend met het vorige tarief (of zonder tarief).`)) remove.mutate(t.id);
                          }}
                        >
                          Verwijderen
                        </ActionButton>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {remove.error && <ErrorNote error={remove.error} />}
      {isAdmin && !tariffs.isLoading && <TariffFormView key={editing?.id ?? 'new'} homeId={homeId} editing={editing} defaultFrom={defaultFrom} hasTariffs={list.length > 0} onDone={() => setEditing(null)} />}
    </section>
  );
}
