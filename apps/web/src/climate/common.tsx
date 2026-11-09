// Small shared pieces for the climate pages (Klimaat, Overzicht, Optimalisatie).
import type { ReactNode } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import type { QueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { qk } from '../api/keys';
import type { Overview, Zone } from '../api/types';
import { ErrorNote } from '../components/bits';
import { useUser, useCan } from '../user';

/** Put a Zone returned by the API (resume, policy, profile, link) into every cache that shows it. */
export function storeZone(qc: QueryClient, homeId: string | null | undefined, z: Zone) {
  qc.setQueryData(qk.zone(z.id), z);
  if (!homeId) {
    qc.invalidateQueries({ queryKey: ['zones'] });
    return;
  }
  qc.setQueryData<Zone[]>(qk.zones(homeId), (zs) => zs?.map((x) => (x.id === z.id ? z : x)));
  qc.setQueryData<Overview>(qk.overview(homeId), (o) => (o ? { ...o, zones: o.zones.map((x) => (x.id === z.id ? z : x)) } : o));
}

/** What the current user may do on the climate pages. Viewers see no controls. Outside a logged-in
 * context (previews, isolated component tests) controls are shown; the server checks every action. */
export function useClimateCan(): { control: boolean; admin: boolean } {
  const user = useUser();
  const can = useCan();
  if (!user) return { control: true, admin: false };
  return { control: can.control, admin: can.admin };
}

/** A router Link that also renders (as a plain link) where there is no router, e.g. in unit tests. */
export function SafeLink({ to, className, children, ...rest }: { to: string; className?: string; children: ReactNode; 'aria-label'?: string }) {
  const inRouter = useInRouterContext();
  if (inRouter) return <Link to={to} className={className} {...rest}>{children}</Link>;
  return <a href={to} className={className} {...rest}>{children}</a>;
}

/** Field names in API validation errors, for Dutch messages. */
const FIELD_LABEL: Record<string, string> = {
  start: 'begintijd',
  end: 'einde',
  target_c: 'doeltemperatuur',
  days: 'dagen',
  label: 'naam',
  name: 'naam',
  mode: 'modus',
  band_c: 'comfortband',
  energy_priority: 'energieprioriteit',
  schedule: 'weekschema',
  variants: 'varianten',
  profile_id: 'profiel',
  zone_id: 'zone',
  min_interval_s: 'minimale tijd tussen wijzigingen',
  max_step_c: 'maximale setpoint-sprong',
  max_starts_per_hour: 'maximaal aantal starts per uur',
  stale_after_s: 'sensor verouderd na',
  plausible_min_c: 'ondergrens sensorwaarden',
  plausible_max_c: 'bovengrens sensorwaarden',
  max_offset_c: 'maximale bijsturing',
  override_default_minutes: 'standaardduur handmatig',
  watchdog_minutes: 'waakhond',
  watchdog_min_progress_c: 'minimale vooruitgang',
};
const TYPE_TEXT: Record<string, string> = {
  string_pattern_mismatch: 'heeft een ongeldige notatie',
  greater_than_equal: 'is te laag',
  greater_than: 'is te laag',
  less_than_equal: 'is te hoog',
  less_than: 'is te hoog',
  string_too_long: 'is te lang',
  string_too_short: 'is te kort',
  too_short: 'heeft te weinig items',
  too_long: 'heeft te veel items',
  missing: 'ontbreekt',
  float_parsing: 'is geen getal',
  int_parsing: 'is geen geheel getal',
  literal_error: 'heeft een onbekende waarde',
  enum: 'heeft een onbekende waarde',
};

interface PydanticError {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
}

/** One Dutch line for a FastAPI/pydantic validation error ({loc, msg, type}). */
export function validationLine(e: PydanticError): string {
  const loc = (e.loc ?? []).filter((x) => x !== 'body');
  const parts: string[] = [];
  for (let i = 0; i < loc.length; i++) {
    const p = loc[i];
    if (typeof p === 'number') {
      const parent = loc[i - 1];
      parts.push(parent === 'blocks' || parent === 'schedule' ? `blok ${p + 1}` : parent === 'variants' ? `variant ${p + 1}` : parent === 'days' ? `dag ${p + 1}` : `${p + 1}`);
    } else if (p !== 'blocks' && p !== 'variants' && p !== 'days') {
      parts.push(FIELD_LABEL[p] ?? p);
    } else if (i === loc.length - 1) {
      parts.push(FIELD_LABEL[p] ?? p);
    }
  }
  const msg = (e.msg ?? '').replace(/^Value error, /, '');
  const text = e.type === 'value_error' ? msg : TYPE_TEXT[e.type ?? ''] ?? 'is ongeldig';
  const where = parts.join(', ');
  const line = where ? `${where}: ${text}` : text;
  return line.charAt(0).toUpperCase() + line.slice(1);
}

/** Error box for forms: the server's Dutch message, and validation details in plain Dutch.
 * (ErrorNote expects detail.errors as strings; FastAPI sends {loc, msg, type} objects.) */
export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ApiError && error.code === 'validation_error' && Array.isArray(error.detail.errors)) {
    const raw = error.detail.errors as unknown[];
    if (raw.some((x) => typeof x === 'object' && x !== null)) {
      const lines = [...new Set(raw.map((x) => (typeof x === 'string' ? x : validationLine(x as PydanticError))))];
      return (
        <div className="note error" role="alert">
          {lines.length === 1 ? lines[0] : (
            <>
              Controleer het volgende:
              <ul className="plain-list">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
            </>
          )}
        </div>
      );
    }
  }
  return <ErrorNote error={error} />;
}

/** "2 uur", "90 min". */
export function durationText(minutes: number): string {
  if (minutes % 60 === 0) return `${minutes / 60} uur`;
  return `${minutes} min`;
}

/** Number field text (accepts a decimal comma) → number, NaN when empty or not a number. */
export function num(v: string): number {
  const t = v.trim().replace(',', '.');
  return t === '' ? NaN : Number(t);
}
