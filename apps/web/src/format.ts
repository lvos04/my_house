import type { Quality } from './api/types';

const nl = 'nl-NL';

export function temp(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '–';
  return v.toLocaleString(nl, { minimumFractionDigits: digits, maximumFractionDigits: digits }) + '°';
}
export function kwh(v: number | null | undefined): string {
  if (v === null || v === undefined) return '– kWh';
  return v.toLocaleString(nl, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' kWh';
}
export function watts(v: number | null | undefined): string {
  if (v === null || v === undefined) return '– W';
  return Math.round(v).toLocaleString(nl) + ' W';
}
export function money(v: number | null | undefined, currency = 'EUR'): string {
  if (v === null || v === undefined) return '';
  return v.toLocaleString(nl, { style: 'currency', currency });
}
export function time(ts: string | Date | null | undefined, tz?: string): string {
  if (!ts) return '–';
  const d = typeof ts === 'string' ? new Date(ts) : ts;
  return d.toLocaleTimeString(nl, { hour: '2-digit', minute: '2-digit', timeZone: tz });
}
export function dateTime(ts: string | null | undefined, tz?: string): string {
  if (!ts) return '–';
  return new Date(ts).toLocaleString(nl, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: tz });
}
export function ago(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return 'nooit';
  const s = Math.max(0, Math.round((now - new Date(ts).getTime()) / 1000));
  if (s < 60) return 'zojuist';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min geleden`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} uur geleden`;
  return `${Math.round(h / 24)} dagen geleden`;
}

export const QUALITY_LABEL: Record<Quality, string> = {
  measured: 'gemeten',
  derived: 'afgeleid',
  estimated: 'geschat',
  missing: 'geen meting',
};

export const MODE_LABEL: Record<string, string> = {
  off: 'Uit',
  heat: 'Verwarmen',
  cool: 'Koelen',
  auto: 'Auto',
  dry: 'Drogen',
  fan: 'Ventileren',
};

export const CONTROL_LABEL: Record<string, string> = {
  schedule: 'Schema',
  manual: 'Handmatig',
  oem_fallback: 'OEM-regeling',
  off: 'Uit',
};

export function localDate(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '–';
  if (n < 1024) return `${n} B`;
  if (n < 1_048_576) return `${(n / 1024).toLocaleString(nl, { maximumFractionDigits: 0 })} kB`;
  if (n < 1_073_741_824) return `${(n / 1_048_576).toLocaleString(nl, { maximumFractionDigits: 1 })} MB`;
  return `${(n / 1_073_741_824).toLocaleString(nl, { maximumFractionDigits: 2 })} GB`;
}
export function dateOnly(ts: string | null | undefined, tz?: string): string {
  if (!ts) return '–';
  return new Date(ts).toLocaleDateString(nl, { day: 'numeric', month: 'long', year: 'numeric', timeZone: tz });
}
export function pct(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined) return '–';
  return (fraction * 100).toLocaleString(nl, { maximumFractionDigits: digits }) + '%';
}
/** "in 5 min" / "over 2 uur" style for future times. */
export function until(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return '–';
  const s = Math.round((new Date(ts).getTime() - now) / 1000);
  if (s <= 0) return 'nu';
  const m = Math.round(s / 60);
  if (m < 60) return `over ${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `over ${h} uur`;
  return `over ${Math.round(h / 24)} dagen`;
}
/** The browser's IANA time zone (e.g. Europe/Amsterdam), or UTC. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}
/** All IANA time zones the browser knows, the browser's own zone always included. */
export function timeZones(): string[] {
  const own = browserTimeZone();
  let all: string[] = [];
  try {
    all = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? [];
  } catch {
    all = [];
  }
  if (!all.length) all = ['Europe/Amsterdam', 'Europe/Brussels', 'Europe/Berlin', 'Europe/London', 'Europe/Paris', 'Europe/Madrid', 'UTC'];
  return all.includes(own) ? all : [own, ...all];
}

/** Web app version (build arg APP_VERSION → VITE_APP_VERSION; vite.config.ts falls back to the VERSION file). */
export const APP_VERSION: string = (import.meta.env.VITE_APP_VERSION as string | undefined) || '0.0.0-dev';
